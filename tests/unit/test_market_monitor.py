"""运行时监控纯函数与事件构造测试（PRD 29.4.1 / 29.8 / 29.9）。"""

from __future__ import annotations

import datetime as dt

import pytest

from aicrypto.domain.market.quality import DataQuality, Severity
from aicrypto.services import market_monitor as mm

UTC = dt.UTC
T = dt.datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def at(minutes: int) -> dt.datetime:
    return T + dt.timedelta(minutes=minutes)


# ---------------------------------------------------------------- 时钟偏移


def test_clock_offset_uses_roundtrip_midpoint() -> None:
    # 本地发出 1000、收到 1200，中点 1100；服务器报 1150 -> 偏移 +50
    assert mm.clock_offset_ms(1150, sent_ms=1000, received_ms=1200) == 50


def test_clock_offset_negative_when_local_ahead() -> None:
    assert mm.clock_offset_ms(900, sent_ms=1000, received_ms=1000) == -100


@pytest.mark.parametrize(
    ("offset", "skewed"),
    [(5000, False), (-5000, False), (5001, True), (-5001, True), (0, False)],
)
def test_is_clock_skewed_boundary(offset: int, skewed: bool) -> None:
    assert mm.is_clock_skewed(offset, threshold_ms=5000) is skewed


# ---------------------------------------------------------------- 延迟


def test_latency_is_processed_minus_exchange() -> None:
    assert mm.latency_ms(at(0).replace(second=2), at(0)) == 2000


# ---------------------------------------------------------------- 缺口


def test_find_gaps_none_for_contiguous() -> None:
    opens = [at(0), at(5), at(10)]
    assert mm.find_open_time_gaps(opens, "5m") == []


def test_find_gaps_counts_missing_bars() -> None:
    opens = [at(0), at(5), at(20)]  # 缺 10、15 两根
    gaps = mm.find_open_time_gaps(opens, "5m")
    assert gaps == [
        {
            "after_open_time": at(5).isoformat(),
            "before_open_time": at(20).isoformat(),
            "missing_bars": 2,
        }
    ]


def test_find_gaps_sorts_input() -> None:
    gaps = mm.find_open_time_gaps([at(20), at(0), at(5)], "5m")
    assert len(gaps) == 1
    assert gaps[0]["missing_bars"] == 2


def test_find_gaps_empty_and_single() -> None:
    assert mm.find_open_time_gaps([], "5m") == []
    assert mm.find_open_time_gaps([at(0)], "5m") == []


# ---------------------------------------------------------------- 事件构造


def test_clock_skew_event_is_critical_stale() -> None:
    ev = mm.build_clock_skew_event(
        market_type="SPOT", offset_ms=7000, threshold_ms=5000, detected_at=T
    )
    assert ev.rule_id == "SYS_CLOCK_SKEW"
    assert ev.severity == Severity.CRITICAL.value
    assert ev.data_quality == DataQuality.STALE.value
    assert ev.detected_by == "SYSTEM"
    assert ev.context == {"clock_offset_ms": 7000, "threshold_ms": 5000}


def test_latency_event_is_warn_degraded() -> None:
    ev = mm.build_latency_event(
        market_type="SPOT",
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=T,
        latency=2500,
        detected_at=T,
    )
    assert ev.rule_id == "DATA_LATENCY_HIGH"
    assert ev.severity == Severity.WARN.value
    assert ev.data_quality == DataQuality.DEGRADED.value
    assert ev.detected_by == "WS"
    assert ev.symbol == "BTCUSDT"
    assert ev.timeframe == "5m"
    assert ev.context["latency_ms"] == 2500


def test_gap_event_is_critical_missing_with_total() -> None:
    gaps = [{"after_open_time": "a", "before_open_time": "b", "missing_bars": 3}]
    ev = mm.build_gap_event(
        market_type="SPOT", symbol="BTCUSDT", timeframe="5m", gaps=gaps, detected_at=T
    )
    assert ev.rule_id == "DATA_GAP"
    assert ev.severity == Severity.CRITICAL.value
    assert ev.data_quality == DataQuality.MISSING.value
    assert ev.context["missing_bars"] == 3
    assert ev.context["check"] == "INCREMENTAL"


def test_rest_unavailable_event_is_critical_stale() -> None:
    ev = mm.build_rest_unavailable_event(
        market_type="SPOT", failures=3, error="boom", detected_at=T
    )
    assert ev.rule_id == "DATA_STALE"
    assert ev.severity == Severity.CRITICAL.value
    assert ev.data_quality == DataQuality.STALE.value
    assert ev.detected_by == "REST"
    assert ev.context["consecutive_failures"] == 3
    assert ev.context["reason"] == "REST_UNAVAILABLE"
