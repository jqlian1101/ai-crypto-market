"""采集器编排测试：恢复先于写入、半开检测、轮换、消息路由（PRD 29.3）。

不连真实 WS / 数据库：直接驱动 ``_recover`` / ``_consume``，DB 与 REST 用假实现。
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
from collections.abc import Iterator
from typing import Any

import pytest

from aicrypto.config.settings import Settings
from aicrypto.infrastructure import binance_ws
from aicrypto.services import market_collector as mc

UTC = dt.UTC
NOW = dt.datetime(2026, 1, 1, 1, 0, 30, tzinfo=UTC)


class FakeRest:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.calls: list[dict[str, Any]] = []

    async def fetch_closed_klines(self, **kw: Any) -> list[dict[str, Any]]:
        self.calls.append(kw)
        self.log.append(f"rest:{kw['symbol']}:{kw['timeframe']}")
        return []


class FakeWs:
    def __init__(self, messages: list[Any], log: list[str]) -> None:
        self._messages = list(messages)
        self.log = log

    async def recv(self) -> Any:
        if not self._messages:
            await asyncio.sleep(3600)
        msg = self._messages.pop(0)
        self.log.append("ws:recv")
        return msg


def make_settings() -> Settings:
    return Settings.model_construct(MARKET_WS_RECONNECT_MAX_MS=30000)


@contextlib.contextmanager
def fake_scope() -> Iterator[Any]:
    yield object()


def make_collector(log: list[str], **kw: Any) -> mc.MarketCollector:
    # 单调时钟必须可控：``_consume`` 用它判断「连接是否达到存活上限」。
    # 若用真实 ``time.monotonic()``，测试里 ``connected_at=0.0`` 会被当成
    # 「已连接 24 小时」，连接立刻被轮换，半开检测根本走不到。
    monotonic = kw.pop("monotonic", lambda: 0.0)
    return mc.MarketCollector(
        settings=make_settings(),
        market_type="USDM_FUTURES",
        symbols=["btcusdt"],
        timeframes=["5m"],
        rest=FakeRest(log),  # type: ignore[arg-type]
        db_scope=fake_scope,
        clock=lambda: NOW,
        monotonic=monotonic,
        **kw,
    )


def kline_msg(*, closed: bool) -> str:
    return json.dumps(
        {
            "stream": "btcusdt@kline_5m",
            "data": {
                "e": "kline",
                "E": 1_767_229_200_000,
                "k": {
                    "t": 1_767_228_900_000,
                    "T": 1_767_229_199_999,
                    "s": "BTCUSDT",
                    "i": "5m",
                    "o": "100",
                    "h": "105",
                    "l": "95",
                    "c": "102",
                    "v": "1",
                    "q": "100",
                    "n": 3,
                    "V": "0.5",
                    "Q": "50",
                    "x": closed,
                },
            },
        }
    )


# ---------------------------------------------------------------- 纯函数


def test_should_rotate_threshold() -> None:
    limit = binance_ws.MAX_CONNECTION_AGE_SECONDS
    assert not mc.should_rotate(0.0, monotonic_now=limit - 1)
    assert mc.should_rotate(0.0, monotonic_now=limit)


def test_symbols_uppercased_and_url_built() -> None:
    c = make_collector([])
    assert c._symbols == ["BTCUSDT"]
    assert "btcusdt@kline_5m" in c._url


# ---------------------------------------------------------------- 恢复顺序


async def test_recover_runs_rest_before_any_ws_write(monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[str] = []
    c = make_collector(log)
    monkeypatch.setattr(c, "_last_open", lambda s, tf: None)
    monkeypatch.setattr(c, "_apply_recovery", lambda *a: log.append("apply"))
    monkeypatch.setattr(c, "_persist", lambda e: log.append("persist") or "INSERTED")

    state = mc._Session(connected_at=0.0, last_message_at=0.0)
    await c._recover(state)
    assert state.recovered
    ws = FakeWs([kline_msg(closed=True)], log)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(c._consume(ws, state), timeout=0.2)  # type: ignore[arg-type]

    assert log.index("rest:BTCUSDT:5m") < log.index("apply") < log.index("persist")


async def test_recover_writes_disconnect_event_only_after_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log: list[str] = []
    c = make_collector(log)
    monkeypatch.setattr(c, "_last_open", lambda s, tf: None)
    monkeypatch.setattr(c, "_apply_recovery", lambda *a: None)
    written: list[dt.datetime] = []
    monkeypatch.setattr(c, "_write_disconnect_event", written.append)

    await c._recover(mc._Session(connected_at=0.0, last_message_at=0.0))
    assert written == []  # 首次连接不算断线

    c._disconnected_at = NOW - dt.timedelta(seconds=10)
    await c._recover(mc._Session(connected_at=0.0, last_message_at=0.0))
    assert written == [NOW - dt.timedelta(seconds=10)]


async def test_recover_window_ends_at_last_closed_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[str] = []
    c = make_collector(log)
    monkeypatch.setattr(c, "_last_open", lambda s, tf: None)
    monkeypatch.setattr(c, "_apply_recovery", lambda *a: None)
    await c._recover(mc._Session(connected_at=0.0, last_message_at=0.0))
    call = c._rest.calls[0]  # type: ignore[attr-defined]
    end_ms = call["end_ms"]
    # 01:00:30 -> 当前 5m 周期起点 01:00:00，未收盘那根被排除
    assert end_ms == int(dt.datetime(2026, 1, 1, 1, 0, tzinfo=UTC).timestamp() * 1000)
    assert end_ms - call["start_ms"] == 20 * 300_000


# ---------------------------------------------------------------- 消费


async def test_half_open_raises_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mc, "HALF_OPEN_TIMEOUT_SECONDS", 0.05)
    c = make_collector([])
    state = mc._Session(connected_at=0.0, last_message_at=0.0)
    with pytest.raises(TimeoutError, match="半开"):
        await c._consume(FakeWs([], []), state)  # type: ignore[arg-type]


async def test_consume_returns_for_rotation() -> None:
    c = make_collector([], monotonic=lambda: float(binance_ws.MAX_CONNECTION_AGE_SECONDS))
    state = mc._Session(connected_at=0.0, last_message_at=0.0)
    await c._consume(FakeWs([], []), state)  # type: ignore[arg-type]


async def test_unclosed_goes_to_cache_not_db(monkeypatch: pytest.MonkeyPatch) -> None:
    cached: list[Any] = []

    async def fake_cache(event: Any) -> str:
        cached.append(event)
        return "k"

    monkeypatch.setattr(mc, "cache_unclosed", fake_cache)
    c = make_collector([])
    monkeypatch.setattr(c, "_persist", lambda e: pytest.fail("未收盘不得落库"))
    state = mc._Session(connected_at=0.0, last_message_at=0.0)
    await c._handle_message(kline_msg(closed=False), state)
    assert len(cached) == 1
    assert state.counters == {"cached_unclosed": 1}


async def test_closed_goes_to_db(monkeypatch: pytest.MonkeyPatch) -> None:
    c = make_collector([])
    persisted: list[Any] = []
    monkeypatch.setattr(c, "_persist", lambda e: persisted.append(e) or "INSERTED")
    state = mc._Session(connected_at=0.0, last_message_at=0.0)
    await c._handle_message(kline_msg(closed=True), state)
    assert persisted[0].row["source"] == "WS"
    assert state.counters == {"INSERTED": 1}


async def test_bad_message_counted_not_raised() -> None:
    c = make_collector([])
    state = mc._Session(connected_at=0.0, last_message_at=0.0)
    await c._handle_message("not json", state)
    await c._handle_message(json.dumps({"data": {"e": "kline", "k": {}}}), state)
    assert state.counters == {"parse_error": 2}


async def test_non_kline_message_ignored() -> None:
    c = make_collector([])
    state = mc._Session(connected_at=0.0, last_message_at=0.0)
    await c._handle_message(json.dumps({"result": None, "id": 1}), state)
    assert state.counters == {}


# ---------------------------------------------------------------- 重连循环


async def test_run_backs_off_and_marks_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    c = make_collector([])
    attempts = {"n": 0}

    async def failing_connection() -> None:
        attempts["n"] += 1
        if attempts["n"] >= 3:
            c.stop()
        raise OSError("boom")

    monkeypatch.setattr(c, "_run_connection", failing_connection)
    monkeypatch.setattr(mc, "reconnect_delay_seconds", lambda attempt, **kw: 0.0)
    await asyncio.wait_for(c.run(), timeout=1.0)
    assert attempts["n"] == 3
    assert c._attempt == 3
    assert c._disconnected_at == NOW
