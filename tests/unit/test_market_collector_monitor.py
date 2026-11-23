"""采集器运行时监控测试：时钟、REST 连续失败、交叉核对、延迟与缺口（PRD 29.4.1 / 29.4.2 / 29.8 / 29.9）。"""

from __future__ import annotations

import contextlib
import datetime as dt
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from aicrypto.config.settings import Settings
from aicrypto.infrastructure.rate_limiter import RateLimitedError
from aicrypto.services import market_collector as mc
from aicrypto.services import market_monitor as mm

UTC = dt.UTC
NOW = dt.datetime(2026, 1, 1, 1, 0, 30, tzinfo=UTC)


@contextlib.contextmanager
def fake_scope() -> Iterator[Any]:
    yield object()


def make_collector(**kw: Any) -> mc.MarketCollector:
    return mc.MarketCollector(
        settings=Settings.model_construct(MARKET_WS_RECONNECT_MAX_MS=30000),
        market_type="USDM_FUTURES",
        symbols=["btcusdt"],
        timeframes=["5m"],
        rest=None,  # type: ignore[arg-type]
        db_scope=fake_scope,
        clock=lambda: NOW,
        **kw,
    )


class ScriptedRest:
    """按脚本返回结果：异常实例抛出，其余作为返回值。"""

    def __init__(self, script: list[Any] | None = None, *, default: Any = None) -> None:
        self.script = list(script or [])
        self.default = default
        self.kline_calls: list[dict[str, Any]] = []
        self.time_calls: list[str] = []

    async def fetch_closed_klines(self, **kw: Any) -> list[dict[str, Any]]:
        self.kline_calls.append(kw)
        return self._next([])

    async def fetch_server_time(self, market_type: str) -> int:
        self.time_calls.append(market_type)
        return self._next(0)

    def _next(self, fallback: Any) -> Any:
        item = self.script.pop(0) if self.script else (self.default or fallback)
        if isinstance(item, Exception):
            raise item
        return item


def _http_error() -> httpx.HTTPError:
    return httpx.ConnectError("down")


def _collector(rest: ScriptedRest) -> mc.MarketCollector:
    c = make_collector()
    c._rest = rest  # type: ignore[assignment]
    return c


# ---------------------------------------------------------------- 时钟 29.8


async def test_sync_clock_records_offset_without_event_when_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rest = ScriptedRest(default=int(NOW.timestamp() * 1000) + 100)
    c = _collector(rest)
    added: list[Any] = []
    monkeypatch.setattr(c, "_add_events", added.extend)
    await c._sync_clock()
    assert rest.time_calls == ["USDM_FUTURES"]
    assert c._clock_offset is not None
    assert abs(c._clock_offset) < 5000
    assert added == []


async def test_sync_clock_skew_emits_critical_event(monkeypatch: pytest.MonkeyPatch) -> None:
    server_ms = int(NOW.timestamp() * 1000) + 60_000  # 偏移约 +60s
    c = _collector(ScriptedRest(default=server_ms))
    added: list[Any] = []
    monkeypatch.setattr(c, "_add_events", lambda evs: added.extend(evs))
    await c._sync_clock()
    assert len(added) == 1
    assert added[0].rule_id == mm.CLOCK_SKEW_RULE_ID


# ---------------------------------------------------------------- REST 连续失败 29.9


async def test_rest_failure_counts_and_alerts_once_at_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    n = mm.REST_FAILURE_THRESHOLD
    rest = ScriptedRest([_http_error()] * (n + 2))
    c = _collector(rest)
    added: list[Any] = []
    monkeypatch.setattr(c, "_add_events", lambda evs: added.extend(evs))
    for _ in range(n + 2):
        with pytest.raises(httpx.HTTPError):
            await c._reconcile_window(
                "BTCUSDT", "5m", start_ms=0, end_ms=300_000, detected_by="REST"
            )
    assert c._rest_failures == n + 2
    assert [e.rule_id for e in added] == [mm.STALE_RULE_ID]


async def test_rest_success_resets_failure_count(monkeypatch: pytest.MonkeyPatch) -> None:
    rest = ScriptedRest([_http_error(), _http_error()])
    c = _collector(rest)
    monkeypatch.setattr(c, "_add_events", lambda evs: None)
    monkeypatch.setattr(c, "_apply_recovery", lambda *a: None)
    for _ in range(2):
        with pytest.raises(httpx.HTTPError):
            await c._reconcile_window(
                "BTCUSDT", "5m", start_ms=0, end_ms=300_000, detected_by="REST"
            )
    assert c._rest_failures == 2
    await c._reconcile_window("BTCUSDT", "5m", start_ms=0, end_ms=300_000, detected_by="REST")
    assert c._rest_failures == 0


async def test_rate_limited_counts_as_rest_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    rest = ScriptedRest([RateLimitedError(418, None)])
    c = _collector(rest)
    monkeypatch.setattr(c, "_add_events", lambda evs: None)
    with pytest.raises(RateLimitedError):
        await c._reconcile_window("BTCUSDT", "5m", start_ms=0, end_ms=300_000, detected_by="REST")
    assert c._rest_failures == 1


async def test_clock_sync_failure_counts_toward_rest_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = _collector(ScriptedRest([_http_error()]))
    monkeypatch.setattr(c, "_add_events", lambda evs: None)
    with pytest.raises(httpx.HTTPError):
        await c._sync_clock()
    assert c._rest_failures == 1


# ---------------------------------------------------------------- 交叉核对 29.4.2


async def test_crosscheck_skipped_when_not_live(monkeypatch: pytest.MonkeyPatch) -> None:
    rest = ScriptedRest()
    c = _collector(rest)
    c._live = False
    await c._crosscheck()
    assert rest.kline_calls == []


async def test_crosscheck_reconciles_recent_window_when_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rest = ScriptedRest()
    c = _collector(rest)
    c._live = True
    applied: list[tuple[str, str, int, str]] = []
    monkeypatch.setattr(
        c,
        "_apply_recovery",
        lambda symbol, tf, start_ms, rows, fetched_at, detected_by: applied.append(
            (symbol, tf, start_ms, detected_by)
        ),
    )
    await c._crosscheck()
    assert len(rest.kline_calls) == 1  # 1 symbol x 1 timeframe
    call = rest.kline_calls[0]
    assert call["end_ms"] - call["start_ms"] == mc.INCREMENTAL_GAP_BARS * 300_000
    assert applied[0][3] == "CROSSCHECK"


async def test_crosscheck_stops_when_live_drops(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _collector(ScriptedRest())
    c._live = True
    c._symbols = ["BTCUSDT", "ETHUSDT"]
    rest = c._rest

    async def flip_live(**kw: Any) -> list[Any]:
        rest.kline_calls.append(kw)
        c._live = False  # WS 断开
        return []

    rest.fetch_closed_klines = flip_live  # type: ignore[method-assign]
    monkeypatch.setattr(c, "_apply_recovery", lambda *a: None)
    await c._crosscheck()
    assert len(rest.kline_calls) == 1


async def test_crosscheck_failure_is_swallowed_and_moves_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rest = ScriptedRest([_http_error()])
    c = _collector(rest)
    c._live = True
    c._symbols = ["BTCUSDT", "ETHUSDT"]
    monkeypatch.setattr(c, "_add_events", lambda evs: None)
    monkeypatch.setattr(c, "_apply_recovery", lambda *a: None)
    await c._crosscheck()  # 不抛出
    assert len(rest.kline_calls) == 2
    assert c._rest_failures == 0  # 第二个请求成功后清零


# ---------------------------------------------------------------- 写入后：延迟 / 缺口 29.8 / 29.4.1


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add_all(self, items: list[Any]) -> None:
        self.added.extend(items)


def _bar(open_time: dt.datetime) -> Any:
    return type("Bar", (), {"open_time": open_time})()


def _row(open_time: dt.datetime, exchange_ts: dt.datetime) -> dict[str, Any]:
    return {
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "open_time": open_time,
        "exchange_ts": exchange_ts,
    }


def _patch_local(monkeypatch: pytest.MonkeyPatch, opens: list[dt.datetime]) -> None:
    monkeypatch.setattr(
        mc,
        "load_local_bars",
        lambda *a, **kw: [_bar(t) for t in opens],
    )


def _at(minutes: int) -> dt.datetime:
    return dt.datetime(2026, 1, 1, tzinfo=UTC) + dt.timedelta(minutes=minutes)


def test_post_insert_latency_under_threshold_no_event(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _collector(ScriptedRest())
    _patch_local(monkeypatch, [_at(0), _at(5)])
    db = _FakeDb()
    events = c._post_insert_checks(db, _row(_at(5), _at(5)), _at(5).replace(second=1))  # type: ignore[arg-type]
    assert events == []
    assert db.added == []


def test_post_insert_latency_over_threshold_emits_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _collector(ScriptedRest())
    _patch_local(monkeypatch, [_at(0), _at(5)])
    db = _FakeDb()
    processed = _at(5) + dt.timedelta(seconds=3)  # 3000ms > 2000ms
    events = c._post_insert_checks(db, _row(_at(5), _at(5)), processed)  # type: ignore[arg-type]
    assert [e.rule_id for e in events] == [mm.LATENCY_RULE_ID]
    assert db.added == events


def test_post_insert_gap_with_previous_bar_emits_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _collector(ScriptedRest())
    _patch_local(monkeypatch, [_at(0), _at(20)])  # 缺 5、10、15
    db = _FakeDb()
    events = c._post_insert_checks(db, _row(_at(20), _at(20)), _at(20))  # type: ignore[arg-type]
    assert [e.rule_id for e in events] == [mm.GAP_RULE_ID]
    assert events[0].context["missing_bars"] == 3


def test_post_insert_contiguous_no_gap_event(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _collector(ScriptedRest())
    _patch_local(monkeypatch, [_at(0), _at(5), _at(10)])
    db = _FakeDb()
    events = c._post_insert_checks(db, _row(_at(10), _at(10)), _at(10))  # type: ignore[arg-type]
    assert events == []


def test_post_insert_first_bar_has_no_gap_check(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _collector(ScriptedRest())
    _patch_local(monkeypatch, [_at(50)])  # 窗口内仅此一根
    db = _FakeDb()
    events = c._post_insert_checks(db, _row(_at(50), _at(50)), _at(50))  # type: ignore[arg-type]
    assert events == []


async def test_persist_records_only_inserted_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _collector(ScriptedRest())
    checks: list[str] = []
    monkeypatch.setattr(
        mc,
        "persist_closed_candle",
        lambda db, row, now: mc.PersistOutcome.UNCHANGED,
    )
    monkeypatch.setattr(c, "_post_insert_checks", lambda *a: checks.append("ran") or [])
    event = type("E", (), {"row": _row(_at(0), _at(0))})()
    assert c._persist(event) == "UNCHANGED"  # type: ignore[arg-type]
    assert checks == []
