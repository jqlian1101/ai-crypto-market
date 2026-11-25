"""异动引擎的衍生品规则族接线测试（PRD 22.2.4 / L-07）。

锁死三条不变量：

1. **衍生品规则真的会被触发**——此前 P2 采集了数据却没有任何消费者，
   ``DER_*`` 规则族完全不存在；
2. **缺失输入标记 skipped**，不按 0 处理——按 0 会让 ``DER_OI_FLUSH``
   在数据缺失时误报，或让 ``DER_FUNDING_EXTREME`` 漏报；
3. **禁用配置生效**——``disabled_rules`` 能屏蔽衍生品规则。
"""

from __future__ import annotations

from decimal import Decimal

from aicrypto.domain.anomaly.engine import (
    DerivativesInputs,
    run_engine,
)
from aicrypto.domain.anomaly.rules import AnomalyHit


def _hits(hits: list[AnomalyHit]) -> list[str]:
    return [hit.rule_id for hit in hits]


def test_oi_surge_produces_hit() -> None:
    """OI 激增被引擎识别。"""
    result = run_engine(
        bars={},
        disabled_rules=[],
        derivatives=DerivativesInputs(oi_change_pct=3.5),
    )
    assert "DER_OI_SURGE" in _hits(result.hits)


def test_oi_flush_produces_hit() -> None:
    """OI 崩塌被引擎识别。"""
    result = run_engine(
        bars={},
        disabled_rules=[],
        derivatives=DerivativesInputs(oi_change_pct=-4.0),
    )
    assert "DER_OI_FLUSH" in _hits(result.hits)


def test_funding_extreme_produces_hit() -> None:
    """资金费率极端被引擎识别。"""
    result = run_engine(
        bars={},
        disabled_rules=[],
        derivatives=DerivativesInputs(funding_rate=0.0008),
    )
    assert "DER_FUNDING_EXTREME" in _hits(result.hits)


def test_funding_flip_produces_hit() -> None:
    """资金费率翻号被引擎识别。"""
    result = run_engine(
        bars={},
        disabled_rules=[],
        derivatives=DerivativesInputs(funding_rate=-0.0001, previous_funding_rate=0.0001),
    )
    assert "DER_FUNDING_FLIP" in _hits(result.hits)


def test_divergence_produces_hit() -> None:
    """价格/OI 背离被引擎识别。"""
    result = run_engine(
        bars={},
        disabled_rules=[],
        derivatives=DerivativesInputs(price_change_pct=-2.0, oi_change_pct=3.0),
    )
    assert "DER_PRICE_OI_DIVERGENCE" in _hits(result.hits)


def test_basis_extreme_produces_hit() -> None:
    """基差极端被引擎识别。"""
    result = run_engine(
        bars={},
        disabled_rules=[],
        derivatives=DerivativesInputs(basis_pct=0.002, basis_percentile=0.97),
    )
    assert "DER_BASIS_EXTREME" in _hits(result.hits)


def test_ls_skew_produces_hit() -> None:
    """多空拥挤被引擎识别。"""
    result = run_engine(
        bars={},
        disabled_rules=[],
        derivatives=DerivativesInputs(skew_percentile=0.98),
    )
    assert "DER_LS_SKEW" in _hits(result.hits)


# --------------------------------------------------------------------------
# 规则 ID 注册完整性
# --------------------------------------------------------------------------


def test_all_produced_rule_ids_are_seeded() -> None:
    """引擎可能产出的每个 rule_id 都必须在规则版本表里注册。

    ``anomaly_events`` 有指向 ``anomaly_rules(rule_id, rule_version)`` 的
    复合外键——漏注册一条，事件落库就会**外键违例**。
    这类错误只在真库跑扫描时才暴露，因此在这里用不变量锁死。
    """
    from aicrypto.domain.anomaly.engine import _DERIVATIVES_RULE_INPUTS
    from aicrypto.services.anomaly_scan import SEED_RULE_IDS

    seed_ids = set(SEED_RULE_IDS)
    missing = sorted(_DERIVATIVES_RULE_INPUTS.keys() - seed_ids)
    assert missing == [], f"衍生品规则未注册到规则版本表: {missing}"


def test_produced_rule_ids_have_version_rows() -> None:
    """产出的 rule_id 必须能解析出版本号（否则事件外键违例）。"""
    from aicrypto.domain.anomaly.engine import _DERIVATIVES_RULE_INPUTS
    from aicrypto.services.anomaly_scan import rule_version_for

    for rule_id in _DERIVATIVES_RULE_INPUTS:
        version = rule_version_for(rule_id)
        assert version in {"v1", "v2"}, f"{rule_id} 没有对应的规则版本行"


def test_funding_extreme_critical_variant_is_seeded() -> None:
    """CRITICAL 变体必须单独注册（它是不同的 rule_id）。"""
    from aicrypto.services.anomaly_scan import SEED_RULE_IDS

    assert "DER_FUNDING_EXTREME" in SEED_RULE_IDS
    assert "DER_FUNDING_EXTREME_CRIT" in SEED_RULE_IDS


# --------------------------------------------------------------------------
# 缺失输入
# --------------------------------------------------------------------------


def test_no_derivatives_data_marks_all_skipped() -> None:
    """未加载衍生品数据时，全部衍生品规则计入 skipped。

    不能静默无输出——那会让人以为「扫描过了、没有异动」，
    实际是「根本没数据可比」。
    """
    result = run_engine(bars={}, disabled_rules=[], derivatives=None)
    skipped = {s.rule_id for s in result.skipped}
    assert "DER_OI_SURGE" in skipped
    assert "DER_FUNDING_EXTREME" in skipped
    assert "DER_PRICE_OI_DIVERGENCE" in skipped
    assert result.hits == []


def test_missing_oi_marks_skipped() -> None:
    """OI 缺失时，OI 相关规则 skipped，不得按 0 判定崩塌。"""
    result = run_engine(
        bars={},
        disabled_rules=[],
        derivatives=DerivativesInputs(funding_rate=0.0001),
    )
    skipped = {s.rule_id for s in result.skipped}
    assert "DER_OI_SURGE" in skipped
    assert "DER_OI_FLUSH" in skipped
    # 缺失 OI 不得产生崩塌误报
    assert "DER_OI_FLUSH" not in _hits(result.hits)


def test_missing_funding_marks_skipped() -> None:
    """费率缺失时标记 skipped，且不产生极端误报。"""
    result = run_engine(
        bars={},
        disabled_rules=[],
        derivatives=DerivativesInputs(oi_change_pct=0.5),
    )
    skipped = {s.rule_id for s in result.skipped}
    assert "DER_FUNDING_EXTREME" in skipped
    assert "DER_FUNDING_EXTREME" not in _hits(result.hits)


def test_missing_divergence_inputs_mark_skipped() -> None:
    """背离的两个输入缺任一都不得判定。"""
    result = run_engine(
        bars={},
        disabled_rules=[],
        derivatives=DerivativesInputs(price_change_pct=-5.0),
    )
    skipped = {s.rule_id for s in result.skipped}
    assert "DER_PRICE_OI_DIVERGENCE" in skipped
    assert "DER_PRICE_OI_DIVERGENCE" not in _hits(result.hits)


# --------------------------------------------------------------------------
# 禁用配置
# --------------------------------------------------------------------------


def test_disabled_derivatives_rule_is_skipped() -> None:
    """禁用规则不产生命中，并计入 skipped。

    记录的是**实际产生但被屏蔽**的那个规则 ID——10% 的 OI 变化直接命中
    ``DER_OI_SURGE_CRIT``，因此 skipped 里出现的是它。
    """
    result = run_engine(
        bars={},
        disabled_rules=["DER_OI_SURGE"],
        derivatives=DerivativesInputs(oi_change_pct=10.0),
    )
    assert "DER_OI_SURGE" not in _hits(result.hits)
    assert "DER_OI_SURGE_CRIT" not in _hits(result.hits)
    skipped = {s.rule_id for s in result.skipped}
    assert "DER_OI_SURGE_CRIT" in skipped
    # 屏蔽原因必须是「被配置禁用」，不是别的
    reasons = {s.rule_id: s.reason for s in result.skipped}
    assert reasons["DER_OI_SURGE_CRIT"] == "DISABLED_BY_CONFIG"


def test_disabled_base_rule_also_blocks_crit_variant() -> None:
    """禁用基础 ID 同时屏蔽其 _CRIT 变体。"""
    result = run_engine(
        bars={},
        disabled_rules=["DER_OI_SURGE"],
        derivatives=DerivativesInputs(oi_change_pct=10.0),
    )
    assert "DER_OI_SURGE_CRIT" not in _hits(result.hits)


def test_disabled_funding_extreme_blocks_hit() -> None:
    result = run_engine(
        bars={},
        disabled_rules=["DER_FUNDING_EXTREME"],
        derivatives=DerivativesInputs(funding_rate=0.002),
    )
    assert "DER_FUNDING_EXTREME" not in _hits(result.hits)


# --------------------------------------------------------------------------
# 阈值可配置
# --------------------------------------------------------------------------


def test_configurable_thresholds_are_respected() -> None:
    """阈值来自配置时，判定随之改变。"""
    from aicrypto.domain.anomaly.derivatives_rules import DerivativesThresholds

    thresholds = DerivativesThresholds(oi_change_warn_pct=10.0, oi_change_critical_pct=20.0)
    result = run_engine(
        bars={},
        disabled_rules=[],
        derivatives=DerivativesInputs(oi_change_pct=5.0),
        derivatives_thresholds=thresholds,
    )
    assert "DER_OI_SURGE" not in _hits(result.hits)


def test_funding_threshold_from_config() -> None:
    from aicrypto.domain.anomaly.derivatives_rules import DerivativesThresholds

    thresholds = DerivativesThresholds(funding_abs_warn=0.01, funding_abs_critical=0.02)
    result = run_engine(
        bars={},
        disabled_rules=[],
        derivatives=DerivativesInputs(funding_rate=Decimal("0.0008")),
        derivatives_thresholds=thresholds,
    )
    assert "DER_FUNDING_EXTREME" not in _hits(result.hits)
