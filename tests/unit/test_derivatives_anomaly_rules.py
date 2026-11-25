"""衍生品异动规则纯函数的测试（PRD 22.2.4）。

这些测试锁死几条容易出错的语义：

1. **缺失数据不得当作 0**——把缺失当 0 会漏报（费率极端）��误报（OI 崩塌）；
2. **方向性**——``DER_OI_SURGE`` 只看上升，``DER_OI_FLUSH`` 只看下降；
   崩塌用 WARN 阈值（−3%）判定，不等到 CRITICAL 阈值（−6%）；
3. **翻号要求双方非零**——一方恰为 0 不算穿越零点；
4. **背离要求双向超阈值**——单边大不足以判定背离。
"""

from __future__ import annotations

from decimal import Decimal

from aicrypto.domain.anomaly.derivatives_rules import (
    DerivativesThresholds,
    evaluate_basis_extreme,
    evaluate_funding_extreme,
    evaluate_funding_flip,
    evaluate_ls_skew,
    evaluate_oi_change,
    evaluate_price_oi_divergence,
)

# --------------------------------------------------------------------------
# OI 激增 / 崩塌
# --------------------------------------------------------------------------


def test_oi_surge_warn() -> None:
    """+3% 触发 WARN。"""
    hits = evaluate_oi_change(oi_change_pct=3.0)
    assert len(hits) == 1
    assert hits[0].rule_id == "DER_OI_SURGE"
    assert hits[0].severity == "WARN"
    assert hits[0].direction == "UP"


def test_oi_surge_critical() -> None:
    """+6% 触发 CRITICAL。"""
    hits = evaluate_oi_change(oi_change_pct=6.0)
    assert len(hits) == 1
    assert hits[0].rule_id == "DER_OI_SURGE_CRIT"
    assert hits[0].severity == "CRITICAL"


def test_oi_below_threshold_no_hit() -> None:
    """+2.9% 不触发。"""
    assert evaluate_oi_change(oi_change_pct=2.9) == []


def test_oi_negative_never_triggers_surge() -> None:
    """负向变化不得触发激增——方向性判定。

    −3% 触发的是**崩塌**（``DER_OI_FLUSH``），不是激增。
    """
    hits = evaluate_oi_change(oi_change_pct=-3.0)
    assert [h.rule_id for h in hits] == ["DER_OI_FLUSH"]
    assert evaluate_oi_change(oi_change_pct=-6.0)[0].rule_id == "DER_OI_FLUSH"
    # −2.9% 不够崩塌阈值，两条规则都不触发
    assert evaluate_oi_change(oi_change_pct=-2.9) == []


def test_oi_flush_is_critical() -> None:
    """−3% 触发崩塌，且一律 CRITICAL（去杠杆需立刻知道）。"""
    hits = evaluate_oi_change(oi_change_pct=-3.0)
    assert len(hits) == 1
    assert hits[0].rule_id == "DER_OI_FLUSH"
    assert hits[0].severity == "CRITICAL"
    assert hits[0].direction == "DOWN"


def test_oi_flush_uses_warn_threshold_not_critical() -> None:
    """崩塌在 −3% 就该触发，不等到 −6%。"""
    hits = evaluate_oi_change(oi_change_pct=-3.5)
    assert len(hits) == 1
    assert hits[0].rule_id == "DER_OI_FLUSH"
    assert hits[0].threshold["oi_change_pct"] == 3.0


def test_oi_just_above_flush_threshold() -> None:
    """−3.1% 仍触发崩塌。"""
    hits = evaluate_oi_change(oi_change_pct=-3.1)
    assert len(hits) == 1
    assert hits[0].rule_id == "DER_OI_FLUSH"


def test_oi_missing_returns_empty() -> None:
    """缺失不是 0——不得当作「没变化」之外的任何判定。"""
    assert evaluate_oi_change(oi_change_pct=None) == []


def test_oi_zero_does_not_trigger_flush() -> None:
    """0% 不得触发崩塌（否则每次没变化都报警）。"""
    assert evaluate_oi_change(oi_change_pct=0.0) == []


def test_oi_thresholds_are_configurable() -> None:
    thresholds = DerivativesThresholds(oi_change_warn_pct=10.0, oi_change_critical_pct=20.0)
    assert evaluate_oi_change(oi_change_pct=5.0, thresholds=thresholds) == []
    hits = evaluate_oi_change(oi_change_pct=12.0, thresholds=thresholds)
    assert hits[0].rule_id == "DER_OI_SURGE"


# --------------------------------------------------------------------------
# 资金费率极端
# --------------------------------------------------------------------------


def test_funding_extreme_warn() -> None:
    hit = evaluate_funding_extreme(funding_rate=Decimal("0.0005"))
    assert hit is not None
    assert hit.rule_id == "DER_FUNDING_EXTREME"
    assert hit.severity == "WARN"


def test_funding_extreme_critical() -> None:
    """CRITICAL 变体带 ``_CRIT`` 后缀——与 ``PRICE_PCT_*`` 约定一致。

    规则版本表按 rule_id 建行，两种级别必须是不同的 rule_id。
    """
    hit = evaluate_funding_extreme(funding_rate=Decimal("0.001"))
    assert hit is not None
    assert hit.rule_id == "DER_FUNDING_EXTREME_CRIT"
    assert hit.severity == "CRITICAL"


def test_funding_extreme_negative_also_triggers() -> None:
    """负向极端同样值得注意——用绝对值判定。"""
    hit = evaluate_funding_extreme(funding_rate=Decimal("-0.0005"))
    assert hit is not None
    assert hit.direction == "DOWN"


def test_funding_extreme_normal_no_hit() -> None:
    assert evaluate_funding_extreme(funding_rate=Decimal("0.0001")) is None


def test_funding_extreme_missing_returns_none() -> None:
    """缺失不是 0——0 是正常费率，缺失必须区分。"""
    assert evaluate_funding_extreme(funding_rate=None) is None


def test_funding_extreme_accepts_float() -> None:
    hit = evaluate_funding_extreme(funding_rate=0.0006)
    assert hit is not None
    assert hit.severity == "WARN"


# --------------------------------------------------------------------------
# 资金费率翻号
# --------------------------------------------------------------------------


def test_funding_flip_positive_to_negative() -> None:
    hit = evaluate_funding_flip(funding_rate=Decimal("-0.0001"), previous_rate=Decimal("0.0001"))
    assert hit is not None
    assert hit.rule_id == "DER_FUNDING_FLIP"
    assert hit.severity == "INFO"
    assert hit.direction == "DOWN"


def test_funding_flip_negative_to_positive() -> None:
    hit = evaluate_funding_flip(funding_rate=Decimal("0.0001"), previous_rate=Decimal("-0.0001"))
    assert hit is not None
    assert hit.direction == "UP"


def test_funding_flip_same_sign_no_hit() -> None:
    assert (
        evaluate_funding_flip(funding_rate=Decimal("0.0002"), previous_rate=Decimal("0.0001"))
        is None
    )


def test_funding_flip_zero_is_not_flip() -> None:
    """一方恰为 0 不算穿越零点。"""
    assert evaluate_funding_flip(funding_rate=Decimal("0"), previous_rate=Decimal("0.0001")) is None
    assert evaluate_funding_flip(funding_rate=Decimal("0.0001"), previous_rate=Decimal("0")) is None


def test_funding_flip_missing_returns_none() -> None:
    assert evaluate_funding_flip(funding_rate=None, previous_rate=Decimal("0.0001")) is None
    assert evaluate_funding_flip(funding_rate=Decimal("0.0001"), previous_rate=None) is None


# --------------------------------------------------------------------------
# 基差极端
# --------------------------------------------------------------------------


def test_basis_extreme_at_p95() -> None:
    hit = evaluate_basis_extreme(basis_pct=0.0012, percentile=0.95)
    assert hit is not None
    assert hit.rule_id == "DER_BASIS_EXTREME"
    assert hit.severity == "WARN"


def test_basis_extreme_below_p95_no_hit() -> None:
    assert evaluate_basis_extreme(basis_pct=0.0012, percentile=0.94) is None


def test_basis_extreme_negative_direction() -> None:
    hit = evaluate_basis_extreme(basis_pct=-0.0012, percentile=0.99)
    assert hit is not None
    assert hit.direction == "DOWN"


def test_basis_extreme_missing_percentile_returns_none() -> None:
    """分位不可得时不能判定——不能用 0 冒充。"""
    assert evaluate_basis_extreme(basis_pct=0.0012, percentile=None) is None
    assert evaluate_basis_extreme(basis_pct=None, percentile=0.99) is None


# --------------------------------------------------------------------------
# 多空拥挤
# --------------------------------------------------------------------------


def test_ls_skew_high_percentile() -> None:
    hit = evaluate_ls_skew(skew_percentile=0.96)
    assert hit is not None
    assert hit.rule_id == "DER_LS_SKEW"
    assert hit.direction == "UP"


def test_ls_skew_low_percentile() -> None:
    """≤P5 同样是极端分位——空头拥挤。"""
    hit = evaluate_ls_skew(skew_percentile=0.04)
    assert hit is not None
    assert hit.direction == "DOWN"


def test_ls_skew_mid_percentile_no_hit() -> None:
    assert evaluate_ls_skew(skew_percentile=0.5) is None


def test_ls_skew_missing_returns_none() -> None:
    assert evaluate_ls_skew(skew_percentile=None) is None


# --------------------------------------------------------------------------
# 价格/OI 背离
# --------------------------------------------------------------------------


def test_divergence_price_up_oi_down() -> None:
    hit = evaluate_price_oi_divergence(price_change_pct=2.0, oi_change_pct=-2.0)
    assert hit is not None
    assert hit.rule_id == "DER_PRICE_OI_DIVERGENCE"
    assert hit.direction == "UP"


def test_divergence_price_down_oi_up() -> None:
    hit = evaluate_price_oi_divergence(price_change_pct=-2.0, oi_change_pct=2.0)
    assert hit is not None
    assert hit.direction == "DOWN"


def test_divergence_same_direction_no_hit() -> None:
    assert evaluate_price_oi_divergence(price_change_pct=2.0, oi_change_pct=2.0) is None
    assert evaluate_price_oi_divergence(price_change_pct=-2.0, oi_change_pct=-2.0) is None


def test_divergence_requires_both_sides_over_threshold() -> None:
    """单边大不足以判定背离——必须双向都超阈值。"""
    assert evaluate_price_oi_divergence(price_change_pct=5.0, oi_change_pct=-0.5) is None
    assert evaluate_price_oi_divergence(price_change_pct=0.5, oi_change_pct=-5.0) is None


def test_divergence_missing_returns_none() -> None:
    assert evaluate_price_oi_divergence(price_change_pct=None, oi_change_pct=2.0) is None
    assert evaluate_price_oi_divergence(price_change_pct=2.0, oi_change_pct=None) is None
