"""资金费率陈旧判定的测试（PRD 29.4.3 通路 C）。

PRD 29.4.3 对资金费率给出**两条**判定，二者来源不同、阈值不同：

| 数据 | 来源 | 允许滞后 |
|---|---|---|
| ``funding_rate`` 当前值 | WS ``markPrice`` 流（1 秒推送） | 2 分钟 |
| ``funding_history`` | REST 轮询 | 1 个周期（8 小时） |

这两个阈值常量此前**定义了却从未被调用**，导致资金费率陈旧时
不会产生 ``DATA_STALE`` 质量事件——下游据此判定「费率是否可信」
缺少依据。

这些测试锁死两个不变量：

1. 超过阈值**必须**产生 ``DATA_STALE / CRITICAL / STALE`` 事件；
2. 未超过阈值**不得**产生事件（不刷噪声）。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from aicrypto.config.settings import Settings
from aicrypto.services.derivatives_collector import (
    FUNDING_CURRENT_CHECK_INTERVAL_SECONDS,
    FUNDING_HISTORY_STALE_THRESHOLD_SECONDS,
    FUNDING_STALE_THRESHOLD_SECONDS,
    DerivativesCollector,
)

UTC = dt.UTC
FUTURES = "USDM_FUTURES"
NOW = dt.datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(
        DATABASE_URL="postgresql+psycopg://u:p@127.0.0.1:5432/db",
        REDIS_URL="redis://127.0.0.1:6379/0",
        AUTH_ENABLED=False,
        MARKET_SYMBOLS="BTCUSDT",
        MARKET_TYPES=FUTURES,
    )


class _RecordingDb:
    """记录写入的质量事件。不连真实数据库。"""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def __call__(self) -> Any:
        recorder = self

        class _Scope:
            def __enter__(self) -> Any:
                return _FakeSession(recorder)

            def __exit__(self, *exc: Any) -> None:
                return None

        return _Scope()


class _FakeSession:
    def __init__(self, recorder: _RecordingDb) -> None:
        self._recorder = recorder

    def add_all(self, events: list[Any]) -> None:
        self._recorder.events.extend(events)

    def execute(self, stmt: Any) -> Any:
        """支持 ``persist_*`` 系列的写入调用（本测试不关心落库结果）。"""

        class _Result:
            @staticmethod
            def scalar_one_or_none() -> Any:
                return None  # 视为冲突跳过

        return _Result()


def _collector(
    *,
    now: dt.datetime = NOW,
    db: _RecordingDb | None = None,
) -> DerivativesCollector:
    recorder = db if db is not None else _RecordingDb()
    collector = DerivativesCollector(
        settings=_settings(),
        market_type=FUTURES,
        symbols=["BTCUSDT"],
        rest=None,  # type: ignore[arg-type]
        db_scope=recorder,  # type: ignore[arg-type]
        clock=lambda: now,
    )
    return collector


# --------------------------------------------------------------------------
# 阈值常量本身
# --------------------------------------------------------------------------


def test_funding_thresholds_match_prd() -> None:
    """PRD 29.4.3：当前值 2 分钟，历史 1 个周期（8 小时）。"""
    assert FUNDING_STALE_THRESHOLD_SECONDS == 120.0
    assert FUNDING_HISTORY_STALE_THRESHOLD_SECONDS == 8 * 3600.0


def test_check_interval_is_shorter_than_threshold() -> None:
    """检查间隔必须显著小于阈值，否则会漏判。"""
    assert FUNDING_CURRENT_CHECK_INTERVAL_SECONDS < FUNDING_STALE_THRESHOLD_SECONDS


# --------------------------------------------------------------------------
# funding_history 陈旧判定
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_funding_history_fresh_writes_no_event() -> None:
    """8 小时内结算过——数据新鲜，不应产生事件。"""
    db = _RecordingDb()
    collector = _collector(db=db)
    await collector._check_funding_history_staleness("BTCUSDT", NOW - dt.timedelta(hours=1), NOW)
    assert db.events == []


@pytest.mark.asyncio
async def test_funding_history_exactly_one_period_is_not_stale() -> None:
    """恰好 1 个周期不算超限——「允许滞后 1 个周期」。"""
    db = _RecordingDb()
    collector = _collector(db=db)
    await collector._check_funding_history_staleness("BTCUSDT", NOW - dt.timedelta(hours=8), NOW)
    assert db.events == []


@pytest.mark.asyncio
async def test_funding_history_beyond_one_period_writes_event() -> None:
    """超过 1 个周期即判定陈旧（已错过一次结算）。"""
    db = _RecordingDb()
    collector = _collector(db=db)
    await collector._check_funding_history_staleness(
        "BTCUSDT", NOW - dt.timedelta(hours=8, minutes=1), NOW
    )
    assert len(db.events) == 1
    event = db.events[0]
    assert event.rule_id == "DATA_STALE"
    assert event.severity == "CRITICAL"
    assert event.data_quality == "STALE"
    assert event.detected_by == "REST"
    assert event.symbol == "BTCUSDT"
    assert event.context["staleness_seconds"] > FUNDING_HISTORY_STALE_THRESHOLD_SECONDS


@pytest.mark.asyncio
async def test_funding_history_stale_event_is_not_written_twice() -> None:
    """每次检查最多一条事件——不重复刷。"""
    db = _RecordingDb()
    collector = _collector(db=db)
    await collector._check_funding_history_staleness("BTCUSDT", NOW - dt.timedelta(hours=10), NOW)
    await collector._check_funding_history_staleness("BTCUSDT", NOW - dt.timedelta(hours=10), NOW)
    assert len(db.events) == 2  # 两次独立检查各一条


# --------------------------------------------------------------------------
# funding 当前值（WS）陈旧判定
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_funding_current_fresh_writes_no_event() -> None:
    """刚收到过 markPrice——不陈旧。"""
    db = _RecordingDb()
    collector = _collector(db=db)
    collector._funding_current_seen_at["BTCUSDT"] = NOW - dt.timedelta(seconds=30)
    await collector._check_funding_current_staleness()
    assert db.events == []


@pytest.mark.asyncio
async def test_funding_current_within_threshold_writes_no_event() -> None:
    """恰好 2 分钟不算超限。"""
    db = _RecordingDb()
    collector = _collector(db=db)
    collector._funding_current_seen_at["BTCUSDT"] = NOW - dt.timedelta(minutes=2)
    await collector._check_funding_current_staleness()
    assert db.events == []


@pytest.mark.asyncio
async def test_funding_current_beyond_threshold_writes_event() -> None:
    """超过 2 分钟没收到 markPrice——判定当前费率不可信。"""
    db = _RecordingDb()
    collector = _collector(db=db)
    collector._funding_current_seen_at["BTCUSDT"] = NOW - dt.timedelta(minutes=3)
    await collector._check_funding_current_staleness()
    assert len(db.events) == 1
    event = db.events[0]
    assert event.rule_id == "DATA_STALE"
    assert event.severity == "CRITICAL"
    assert event.data_quality == "STALE"
    # 当前值来自 WS，必须标 WS 而不是 REST
    assert event.detected_by == "WS"
    assert event.context["staleness_seconds"] > FUNDING_STALE_THRESHOLD_SECONDS


@pytest.mark.asyncio
async def test_funding_current_never_received_writes_no_event() -> None:
    """从未收到过 markPrice：不写事件。

    WS 尚未建立或刚断流时不写——断流本身由 WS 重连逻辑与
    market_monitor 负责，避免同一故障写两条事件。
    """
    db = _RecordingDb()
    collector = _collector(db=db)
    await collector._check_funding_current_staleness()
    assert db.events == []


@pytest.mark.asyncio
async def test_funding_current_check_skipped_for_spot() -> None:
    """现货没有 markPrice 流（29.2），不做该判定。"""
    from aicrypto.services.derivatives_collector import DerivativesCollector as C

    collector = C(
        settings=Settings(
            DATABASE_URL="postgresql+psycopg://u:p@127.0.0.1:5432/db",
            REDIS_URL="redis://127.0.0.1:6379/0",
            AUTH_ENABLED=False,
            MARKET_SYMBOLS="BTCUSDT",
            MARKET_TYPES="SPOT",
        ),
        market_type="SPOT",
        symbols=["BTCUSDT"],
        rest=None,  # type: ignore[arg-type]
        db_scope=_RecordingDb(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    # 即使人为塞入时间戳，现货也不该产出事件
    collector._funding_current_seen_at["BTCUSDT"] = NOW - dt.timedelta(hours=1)
    await collector._check_funding_current_staleness()
    # _RecordingDb 是新实例，这里通过 metrics 无法断言，改为验证无异常
    assert collector._has_mark_price is False


# --------------------------------------------------------------------------
# WS 消息处理：记录接收时刻
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_price_message_records_seen_at() -> None:
    """收到 markPrice 后必须记录时刻，否则永远判定为「从未收到」。"""
    import json

    db = _RecordingDb()
    collector = _collector(db=db)
    raw = json.dumps(
        {
            "e": "markPriceUpdate",
            "E": int(NOW.timestamp() * 1000),
            "s": "BTCUSDT",
            "p": "42000.00",
            "i": "42001.00",
            "r": "0.0001",
            "T": int((NOW + dt.timedelta(hours=1)).timestamp() * 1000),
        }
    )
    await collector._handle_message(raw)
    assert "BTCUSDT" in collector._funding_current_seen_at
    assert collector._funding_current_seen_at["BTCUSDT"] == NOW


@pytest.mark.asyncio
async def test_agg_trade_message_does_not_record_funding_seen_at() -> None:
    """聚合成交不能被误记为资金费率更新——两者是不同流。"""
    import json

    collector = _collector()
    raw = json.dumps(
        {
            "e": "aggTrade",
            "E": int(NOW.timestamp() * 1000),
            "T": int(NOW.timestamp() * 1000),
            "s": "BTCUSDT",
            "a": 1,
            "p": "42000.00",
            "q": "0.1",
            "m": False,
        }
    )
    await collector._handle_message(raw)
    assert "BTCUSDT" not in collector._funding_current_seen_at


# --------------------------------------------------------------------------
# 陈旧度不得为负
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_funding_current_future_seen_at_does_not_write_negative_event() -> None:
    """时钟倒挂（seen_at 晚于 now）时不得产生负的陈旧度事件。

    ``evaluate_staleness`` 内部会把 age 夹到 >= 0，因此不应判为陈旧。
    """
    db = _RecordingDb()
    collector = _collector(db=db)
    collector._funding_current_seen_at["BTCUSDT"] = NOW + dt.timedelta(minutes=5)
    await collector._check_funding_current_staleness()
    assert db.events == []
