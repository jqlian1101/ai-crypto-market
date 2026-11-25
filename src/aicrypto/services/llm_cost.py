"""LLM 成本计算（PRD 24.2 / 30.4）。

**本模块是成本口径的唯一实现**。成本数据只在「模型调用当场」捕获，
事后无法补——token 数只有供应商知道。

**关键设计：单价快照随成本记录一起落库**。

``llm_cost_records.price_snapshot`` 存的是**当时生效的单价**（含单价版本号），
而不是「按当前单价表回查」。这样：

- 改了单价表，历史成本**不会**被重新计算、不会漂移；
- 成本报表可以按 ``pricing_version`` 分组，对比改价前后的影响；
- 单价表缺失某模型时，只要快照在，历史成本依然可复算。

**没有单价时的行为**：返回 ``None`` 并标记为「未知成本」，
**绝不按 0 计费**。按 0 计费会让成本报表静默低估——
这与 L-05「键缺失 ≠ 费率为 0」是同一条原则。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicrypto.infrastructure.database.models.analysis import ModelPricing

#: 单价单位：USD / 百万 token
TOKENS_PER_MTOK = Decimal(1_000_000)

#: 成本精度：美元保留 10 位小数（30.1 的 MoneyType 口径 NUMERIC(38, 8) 之上再留余量）
COST_QUANTUM = Decimal("0.0000000001")


@dataclass(frozen=True, slots=True)
class PricingEntry:
    """一条模型单价。"""

    model_id: str
    provider: str
    pricing_version: str
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    currency: str = "USD"

    def snapshot(self) -> dict[str, Any]:
        """生成落库用的单价快照。

        Decimal 序列化为**字符串**（30.2：不经浮点中转）。
        """
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "pricing_version": self.pricing_version,
            "input_usd_per_mtok": str(self.input_usd_per_mtok),
            "output_usd_per_mtok": str(self.output_usd_per_mtok),
            "currency": self.currency,
        }


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """一次模型调用的成本估算结果。"""

    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    pricing_version: str | None
    price_snapshot: dict[str, Any] | None

    @property
    def is_priced(self) -> bool:
        """是否成功定价。

        ``False`` 表示**单价缺失**——成本未知，不代表成本为 0。
        """
        return self.cost_usd is not None


def estimate_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    pricing: PricingEntry | None,
) -> CostEstimate:
    """估算一次模型调用的成本。

    ``pricing`` 为 ``None``（单价表里没有该模型）时返回
    ``cost_usd=None``，**不返回 0**。
    """
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token 数不能为负")
    if pricing is None:
        return CostEstimate(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=None,
            pricing_version=None,
            price_snapshot=None,
        )
    input_cost = Decimal(input_tokens) * pricing.input_usd_per_mtok / TOKENS_PER_MTOK
    output_cost = Decimal(output_tokens) * pricing.output_usd_per_mtok / TOKENS_PER_MTOK
    total = (input_cost + output_cost).quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)
    return CostEstimate(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=total,
        pricing_version=pricing.pricing_version,
        price_snapshot=pricing.snapshot(),
    )


def resolve_pricing(
    db: Session,
    *,
    model_id: str,
    as_of: dt.datetime,
) -> PricingEntry | None:
    """从 ``model_pricing`` 表解析某模型在 ``as_of`` 时点生效的单价。

    取 ``effective_from <= as_of`` 的最新一行。同一个模型可能有多行
    （改价产生新版本），因此按 ``effective_from`` 倒序取第一条。

    返回 ``None`` 表示该模型没有登记单价——调用方应记录「成本未知」，
    不能按 0 计费。
    """
    statement = (
        select(ModelPricing)
        .where(
            ModelPricing.model_id == model_id,
            ModelPricing.effective_from <= as_of,
        )
        .order_by(ModelPricing.effective_from.desc(), ModelPricing.pricing_version.desc())
        .limit(1)
    )
    row = db.execute(statement).scalar_one_or_none()
    if row is None:
        return None
    return PricingEntry(
        model_id=row.model_id,
        provider=row.provider,
        pricing_version=row.pricing_version,
        input_usd_per_mtok=row.input_usd_per_mtok,
        output_usd_per_mtok=row.output_usd_per_mtok,
        currency=row.currency,
    )


__all__ = [
    "COST_QUANTUM",
    "TOKENS_PER_MTOK",
    "CostEstimate",
    "PricingEntry",
    "estimate_cost",
    "resolve_pricing",
]
