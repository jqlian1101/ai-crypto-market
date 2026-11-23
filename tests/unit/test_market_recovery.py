"""REST 对账与恢复纯函数测试（PRD 29.3.2 / 29.4.2）。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from aicrypto.services import market_recovery as mr

UTC = dt.UTC
T0 = dt.datetime(2026, 1, 1, tzinfo=UTC)
STEP_5M = dt.timedelta(minutes=5)


def bar(i: int, close: str = "100", source: str = "WS") -> mr.LocalBar:
    return mr.LocalBar(open_time=T0 + i * STEP_5M, close=Decimal(close), source=source)


def remote_row(i: int, close: str = "100", *, valid: bool = True) -> dict[str, Any]:
    high = Decimal("50") if not valid else Decimal("200")
    return {
        "open_time": T0 + i * STEP_5M,
        "close_time": T0 + (i + 1) * STEP_5M - dt.timedelta(milliseconds=1),
        "open": Decimal("100"),
        "high": high,
        "low": Decimal("90"),
        "close": Decimal(close),
        "volume": Decimal("1"),
        "quote_volume": Decimal("1"),
        "source": "REST",
    }


# ---------------------------------------------------------------- recovery_window_bars


def test_window_cold_start_uses_lookback() -> None:
    assert mr.recovery_window_bars(last_local_open=None, end_ms=0, timeframe="5m") == 20


def test_window_expands_for_long_gap() -> None:
    last = T0
    end_ms = int((T0 + 100 * STEP_5M).timestamp() * 1000)
    # 缺口 ~99 根 + 5 padding
    n = mr.recovery_window_bars(last_local_open=last, end_ms=end_ms, timeframe="5m")
    assert n == 104


def test_window_keeps_lookback_for_short_gap() -> None:
    last = T0
    end_ms = int((T0 + 2 * STEP_5M).timestamp() * 1000)
    assert mr.recovery_window_bars(last_local_open=last, end_ms=end_ms, timeframe="5m") == 20


def test_window_capped_by_max() -> None:
    last = T0
    end_ms = int((T0 + 100_000 * STEP_5M).timestamp() * 1000)
    assert (
        mr.recovery_window_bars(last_local_open=last, end_ms=end_ms, timeframe="5m", max_bars=50)
        == 50
    )


# ---------------------------------------------------------------- reconcile


def test_reconcile_clean_when_identical() -> None:
    plan = mr.reconcile([bar(0), bar(1)], [remote_row(0), remote_row(1)])
    assert plan.is_clean


def test_reconcile_missing_local_bar_becomes_write() -> None:
    plan = mr.reconcile([bar(0)], [remote_row(0), remote_row(1)])
    assert [r["open_time"] for r in plan.missing] == [T0 + STEP_5M]
    assert len(plan.writes) == 1


def test_reconcile_divergence_overwritten_by_rest() -> None:
    plan = mr.reconcile([bar(0, close="100")], [remote_row(0, close="101")])
    assert len(plan.divergent) == 1
    local, row = plan.divergent[0]
    assert local.close == Decimal("100")
    assert row["close"] == Decimal("101")
    assert plan.writes == [row]


def test_reconcile_tick_tolerance_ignores_small_diff() -> None:
    plan = mr.reconcile(
        [bar(0, close="100.00")],
        [remote_row(0, close="100.01")],
        tick_size=Decimal("0.01"),
    )
    assert plan.divergent == []
    assert plan.is_clean


def test_reconcile_tick_tolerance_flags_larger_diff() -> None:
    plan = mr.reconcile(
        [bar(0, close="100.00")],
        [remote_row(0, close="100.02")],
        tick_size=Decimal("0.01"),
    )
    assert len(plan.divergent) == 1


def test_reconcile_local_only_inside_window_flagged_not_deleted() -> None:
    plan = mr.reconcile([bar(0), bar(1), bar(2)], [remote_row(0), remote_row(2)])
    assert [b.open_time for b in plan.local_only] == [T0 + STEP_5M]
    # 只标记，不进入 writes（不删除、不覆盖）
    assert plan.writes == []


def test_reconcile_local_older_than_remote_window_not_flagged() -> None:
    plan = mr.reconcile([bar(0), bar(5)], [remote_row(5), remote_row(6)])
    assert plan.local_only == []


def test_reconcile_invalid_remote_rejected_and_not_written() -> None:
    plan = mr.reconcile([], [remote_row(0, valid=False)])
    assert len(plan.invalid_remote) == 1
    assert plan.missing == []
    assert plan.writes == []


# ---------------------------------------------------------------- missing_runs


def test_missing_runs_merges_consecutive() -> None:
    times = [T0, T0 + STEP_5M, T0 + 3 * STEP_5M]
    runs = mr.missing_runs(times, "5m")
    assert runs == [
        (T0.isoformat(), (T0 + STEP_5M).isoformat(), 2),
        ((T0 + 3 * STEP_5M).isoformat(), (T0 + 3 * STEP_5M).isoformat(), 1),
    ]


def test_missing_runs_empty() -> None:
    assert mr.missing_runs([], "5m") == []


# ---------------------------------------------------------------- events


def _common(**kw: Any) -> dict[str, Any]:
    base = {
        "market_type": "USDM_FUTURES",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "detected_by": "REST",
        "detected_at": T0,
    }
    base.update(kw)
    return base


def test_events_missing_uses_gap_rule_with_severity_by_count() -> None:
    plan = mr.reconcile([], [remote_row(i) for i in range(3)])
    events = mr.build_plan_events(plan, **_common())
    gap = [e for e in events if e.rule_id == "DATA_GAP"]
    assert len(gap) == 1
    assert gap[0].severity == "CRITICAL"  # 3 根 >= critical_bars
    assert gap[0].data_quality == "MISSING"
    assert gap[0].context is not None
    assert gap[0].context["missing_bars"] == 3


def test_events_one_per_category() -> None:
    plan = mr.reconcile(
        [bar(0, close="1"), bar(1), bar(2)],
        [remote_row(0, close="2"), remote_row(3), remote_row(2)],
    )
    events = mr.build_plan_events(plan, **_common())
    rules = sorted(e.rule_id for e in events)
    assert rules == ["DATA_GAP", "DATA_QUALITY_DEGRADED", "DATA_SOURCE_DIVERGENCE"]


def test_events_empty_for_clean_plan() -> None:
    plan = mr.reconcile([bar(0)], [remote_row(0)])
    assert mr.build_plan_events(plan, **_common()) == []


def test_disconnect_event_fields() -> None:
    down = T0
    up = T0 + dt.timedelta(seconds=42)
    event = mr.build_disconnect_event(
        market_type="SPOT", disconnected_at=down, reconnected_at=up, reason="half_open"
    )
    assert event.rule_id == "DATA_WS_DISCONNECT"
    assert event.severity == "WARN"
    assert event.symbol is None
    assert event.context is not None
    assert event.context["downtime_seconds"] == 42.0
    assert event.context["reason"] == "half_open"
