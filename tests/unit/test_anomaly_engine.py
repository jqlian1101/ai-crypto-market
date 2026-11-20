"""规则引擎测试：disabled_rules 执行与错误隔离（PRD 22.2 / 29.2.1）。"""

from __future__ import annotations

from decimal import Decimal

from aicrypto.domain.anomaly.engine import (
    UNIMPLEMENTED_1M_RULES,
    BarWindow,
    run_engine,
)

D = Decimal
ONE_M_DISABLED = sorted(UNIMPLEMENTED_1M_RULES)


def _flat_window(timeframe: str, n: int = 120) -> BarWindow:
    """平稳行情：不应命中任何规则。"""
    price = [D("100")] * n
    return BarWindow(
        timeframe=timeframe,
        opens=price,
        highs=[D("100.1")] * n,
        lows=[D("99.9")] * n,
        closes=price,
        volumes=[D("10")] * n,
    )


def _big_move_window(timeframe: str, n: int = 120) -> BarWindow:
    """窗口首尾涨幅 3%，足以触发 5m CRITICAL（阈值 1.20%）。"""
    opens = [D("100")] * n
    closes = [D("100")] * (n - 1) + [D("103")]
    highs = [D("104")] * n
    lows = [D("99")] * n
    return BarWindow(
        timeframe=timeframe,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=[D("10")] * n,
    )


def test_disabled_1m_rules_are_reported_skipped_not_errors() -> None:
    result = run_engine(bars={}, disabled_rules=ONE_M_DISABLED)
    assert result.errors == []


def test_unimplemented_1m_rule_not_disabled_is_error() -> None:
    """29.2.1：未禁用且未实现的 1m 规则必须报错，不能静默失效。"""
    result = run_engine(bars={}, disabled_rules=[])
    error_ids = {e.rule_id for e in result.errors}
    assert error_ids == UNIMPLEMENTED_1M_RULES


def test_missing_timeframe_is_skipped_with_reason() -> None:
    result = run_engine(bars={}, disabled_rules=ONE_M_DISABLED)
    skipped = {s.rule_id: s.reason for s in result.skipped}
    assert skipped["PRICE_PCT_5M"] == "NO_BARS"
    assert skipped["VOL_ATR_SPIKE"] == "NO_BARS"


def test_flat_market_produces_no_hits() -> None:
    bars = {"5m": _flat_window("5m"), "15m": _flat_window("15m"), "1h": _flat_window("1h")}
    result = run_engine(bars=bars, disabled_rules=ONE_M_DISABLED)
    assert result.hits == []


def test_price_move_hit_produces_critical_event() -> None:
    bars = {"5m": _big_move_window("5m")}
    result = run_engine(bars=bars, disabled_rules=ONE_M_DISABLED)
    hits = [h for h in result.hits if h.rule_id.startswith("PRICE_PCT_5M")]
    assert len(hits) == 1
    assert hits[0].rule_id == "PRICE_PCT_5M_CRIT"
    assert hits[0].severity == "CRITICAL"
    assert hits[0].direction == "UP"


def test_disabling_base_id_also_silences_critical_variant() -> None:
    bars = {"5m": _big_move_window("5m")}
    result = run_engine(
        bars=bars,
        disabled_rules=[*ONE_M_DISABLED, "PRICE_PCT_5M"],
    )
    assert not [h for h in result.hits if h.rule_id.startswith("PRICE_PCT_5M")]
    skipped = {s.rule_id: s.reason for s in result.skipped}
    assert skipped["PRICE_PCT_5M"] == "DISABLED_BY_CONFIG"


def test_disabled_rule_is_recorded_not_silently_dropped() -> None:
    bars = {"5m": _big_move_window("5m")}
    result = run_engine(bars=bars, disabled_rules=[*ONE_M_DISABLED, "PRICE_PCT_5M"])
    assert any(s.rule_id == "PRICE_PCT_5M" for s in result.skipped)


def test_single_rule_exception_does_not_block_others() -> None:
    """单条规则抛错时计入 errors，其余规则仍执行。"""
    # 最新成交量放大 10 倍才会进入方向计算分支；此时 opens 为空会在 opens[-1] 抛 IndexError。
    volumes = [D("1")] * 119 + [D("10")]
    bad_hour = BarWindow(
        timeframe="1h",
        opens=[],
        highs=[D("1")] * 120,
        lows=[D("1")] * 120,
        closes=[D("1")] * 120,
        volumes=volumes,
    )
    bars = {"5m": _big_move_window("5m"), "1h": bad_hour}
    result = run_engine(bars=bars, disabled_rules=ONE_M_DISABLED)
    assert any(h.rule_id.startswith("PRICE_PCT_5M") for h in result.hits)
    assert any(e.rule_id == "VOL_REL_VOLUME" for e in result.errors)
