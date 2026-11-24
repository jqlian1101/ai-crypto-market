"""衍生品与盘口入库的幂等性与陈旧判定测试（PRD 29.4.3 / 29.7）。

纯逻辑部分不需要数据库；写入幂等性在集成测试中验证（需要 PostgreSQL）。
"""

from __future__ import annotations

import datetime as dt

from aicrypto.services.derivatives_ingest import (
    StaleCheck,
    build_stale_event,
    evaluate_staleness,
)

UTC = dt.UTC
BASE = dt.datetime(2026, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------------
# 陈旧判定（29.4.3 通路 C）
# --------------------------------------------------------------------------


def test_stale_check_not_stale_within_threshold() -> None:
    check = evaluate_staleness(
        BASE, fetched_at=BASE + dt.timedelta(seconds=60), threshold_seconds=180
    )
    assert check.stale is False
    assert check.staleness_seconds == 60.0


def test_stale_check_stale_beyond_threshold() -> None:
    check = evaluate_staleness(
        BASE, fetched_at=BASE + dt.timedelta(seconds=200), threshold_seconds=180
    )
    assert check.stale is True
    assert check.staleness_seconds == 200.0


def test_stale_check_never_negative() -> None:
    """业务时间晚于拉取时间（时钟漂移）时不产生负数年龄。"""
    check = evaluate_staleness(
        BASE + dt.timedelta(seconds=30), fetched_at=BASE, threshold_seconds=180
    )
    assert check.staleness_seconds == 0.0
    assert check.stale is False


def test_stale_check_custom_rule_id() -> None:
    check = evaluate_staleness(
        BASE,
        fetched_at=BASE + dt.timedelta(seconds=999),
        threshold_seconds=1,
        rule_id="DATA_STALE_FUNDING",
    )
    assert check.rule_id == "DATA_STALE_FUNDING"


def test_build_stale_event_shape() -> None:
    check = StaleCheck(stale=True, staleness_seconds=300.0, threshold_seconds=180.0)
    event = build_stale_event(
        market_type="USDM_FUTURES",
        symbol="BTCUSDT",
        check=check,
        detected_at=BASE,
    )
    assert event.rule_id == "DATA_STALE"
    assert event.severity == "CRITICAL"
    assert event.data_quality == "STALE"
    assert event.detected_by == "REST"
    assert event.market_type == "USDM_FUTURES"
    assert event.symbol == "BTCUSDT"
    assert event.context["staleness_seconds"] == 300.0
    assert event.context["threshold_seconds"] == 180.0
    assert event.context["reason"] == "POLLED_DATA_STALE"
    assert event.id.startswith("dqe_")
