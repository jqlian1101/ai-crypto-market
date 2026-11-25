"""LLM 成本计算的测试（PRD 24.2 / 30.4）。

**背景**��``llm_cost_records`` 表已存在但**没有任何写入方**，
成本分析无从谈起。本测试锁死成本口径。

关键不变量：

1. **单价缺失 ≠ 成本为 0** —— 返回 ``None`` 并标记「未定价」。
   按 0 计费会让成本报表静默低估，与 L-05「键缺失 ≠ 费率为 0」同源；
2. **单价快照随成本落库** —— 改单价表不会让历史成本漂移；
3. Decimal **不经浮点中转** —— 单价与费用全程 Decimal。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aicrypto.services.llm_cost import (
    TOKENS_PER_MTOK,
    CostEstimate,
    PricingEntry,
    estimate_cost,
)


def _pricing(
    *,
    input_rate: str = "3.0",
    output_rate: str = "15.0",
    version: str = "v1",
) -> PricingEntry:
    return PricingEntry(
        model_id="test-model",
        provider="cloud",
        pricing_version=version,
        input_usd_per_mtok=Decimal(input_rate),
        output_usd_per_mtok=Decimal(output_rate),
    )


# --------------------------------------------------------------------------
# 基础计算
# --------------------------------------------------------------------------


def test_cost_formula() -> None:
    """1000 输入 token + 500 输出 token。

    输入: 1000/1e6 * 3.0 = 0.003
    输出:  500/1e6 * 15.0 = 0.0075
    合计: 0.0105
    """
    estimate = estimate_cost(
        input_tokens=1000,
        output_tokens=500,
        pricing=_pricing(),
    )
    assert estimate.cost_usd == Decimal("0.0105")
    assert estimate.is_priced is True


def test_cost_zero_tokens_is_zero() -> None:
    """0 token 的费用确实是 0——这与「单价缺失」是两件事。"""
    estimate = estimate_cost(input_tokens=0, output_tokens=0, pricing=_pricing())
    assert estimate.cost_usd == Decimal(0)
    assert estimate.is_priced is True


def test_cost_uses_decimal_not_float() -> None:
    """单价与 token 数全程 Decimal，不经浮点。"""
    estimate = estimate_cost(
        input_tokens=1_000_000,
        output_tokens=0,
        pricing=_pricing(input_rate="0.1"),
    )
    assert estimate.cost_usd == Decimal("0.1")
    assert isinstance(estimate.cost_usd, Decimal)


def test_negative_tokens_rejected() -> None:
    """负 token 数必须报错。"""
    with pytest.raises(ValueError):
        estimate_cost(input_tokens=-1, output_tokens=0, pricing=_pricing())


# --------------------------------------------------------------------------
# 单价缺失：绝不能按 0 计费
# --------------------------------------------------------------------------


def test_missing_pricing_yields_none_not_zero() -> None:
    """单价缺失时成本是 ``None``，**不是 0**。

    这是本模块最关键的一条不变量：按 0 计费会让成本报表静默低估。
    """
    estimate = estimate_cost(input_tokens=1000, output_tokens=500, pricing=None)
    assert estimate.cost_usd is None
    assert estimate.is_priced is False


def test_missing_pricing_has_no_snapshot() -> None:
    """单价缺失时快照也是 ``None``——不能伪造一份单价快照。"""
    estimate = estimate_cost(input_tokens=1000, output_tokens=500, pricing=None)
    assert estimate.price_snapshot is None
    assert estimate.pricing_version is None


def test_missing_pricing_still_records_tokens() -> None:
    """即使没定价，token 数仍要如实记录——用量是客观事实。"""
    estimate = estimate_cost(input_tokens=1234, output_tokens=567, pricing=None)
    assert estimate.input_tokens == 1234
    assert estimate.output_tokens == 567


# --------------------------------------------------------------------------
# 单价快照
# --------------------------------------------------------------------------


def test_snapshot_contains_pricing_version() -> None:
    """快照必须含单价版本号，否则改价后无法追溯当时按什么价算的。"""
    snapshot = _pricing(version="v2").snapshot()
    assert snapshot["pricing_version"] == "v2"


def test_snapshot_serializes_decimal_as_string() -> None:
    """Decimal 序列化为字符串（30.2：不经浮点中转）。"""
    snapshot = _pricing(input_rate="0.0000015").snapshot()
    assert snapshot["input_usd_per_mtok"] == "0.0000015"
    assert isinstance(snapshot["input_usd_per_mtok"], str)


def test_snapshot_roundtrip_is_lossless() -> None:
    """快照往返无损——高精度单价不能被截断。"""
    entry = _pricing(input_rate="0.000000000001")
    snapshot = entry.snapshot()
    assert Decimal(snapshot["input_usd_per_mtok"]) == entry.input_usd_per_mtok


def test_snapshot_records_currency() -> None:
    assert _pricing().snapshot()["currency"] == "USD"


def test_cost_estimate_carries_snapshot() -> None:
    """成本估算结果带上单价快照，供调用方一起落库。"""
    estimate = estimate_cost(input_tokens=100, output_tokens=100, pricing=_pricing())
    assert estimate.price_snapshot is not None
    assert estimate.price_snapshot["model_id"] == "test-model"


def test_pricing_version_carried_on_estimate() -> None:
    estimate = estimate_cost(input_tokens=100, output_tokens=100, pricing=_pricing(version="v3"))
    assert estimate.pricing_version == "v3"


# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------


def test_tokens_per_mtok() -> None:
    assert Decimal(1_000_000) == TOKENS_PER_MTOK


def test_cost_estimate_is_frozen() -> None:
    """成本估算结果不可变——落库后不应被调用方改写。"""
    estimate = CostEstimate(
        input_tokens=1,
        output_tokens=1,
        cost_usd=Decimal("0.1"),
        pricing_version="v1",
        price_snapshot={},
    )
    with pytest.raises(AttributeError):
        estimate.input_tokens = 2  # type: ignore[misc]
