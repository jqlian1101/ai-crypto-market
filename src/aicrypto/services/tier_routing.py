"""模型分层的档位路由（PRD 24.1 / 24.3、30.3）。

**为什么单独成模块**：档位路由是纯决策逻辑——「这次分析该用哪一档」
「这一档该用哪个模型」「本地挂了要不要回退云端」——都不涉及 IO。
把它从 ``analysis_worker`` 里拆出来，才能用纯单元测试把
**降级链的每一种组合**都覆盖到。

**分层的意义**（PRD 24.1）：不同任务对准确率的要求差一个数量级。

| 档位 | 承担 | 模型要求 |
|---|---|---|
| ``TIER_LIGHT`` | 抽取 / 分类 / 摘要等子 Agent | 最便宜即可 |
| ``TIER_STANDARD`` | Master 汇总（Bias / 概率 / 置信度） | 唯一值得为准确率付溢价的档 |
| ``TIER_DEEP`` | 日报 / 周报 / 分歧复盘 | 量小但准确率杠杆最高，**不要降级这一档** |

**两条不能破的规则**：

1. **档位必须显式记录**。``model_runs`` / ``llm_cost_records`` 的
   ``tier`` 列是成本报表按档位分组的唯一依据——不记档位，
   报表就分不出「哪一档花了多少钱」，分层也就白做了。

2. **降级必须留痕**。本地模型不可用时回退云端、或某档没配模型
   回退到上一档，都要在输出里**标注 degraded 与原因**。
   静默降级是最危险的：读者会以为拿到的是 DEEP 档的结论，
   实际可能是 LIGHT 档跑的。**沉默的降级比显式的失败更坏**——
   至少显式的失败让人知道该去查什么。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from aicrypto.common.errors import AICryptoError, ErrorCode
from aicrypto.config.settings import Settings
from aicrypto.domain.analysis.agent_output import ModelTier


class TierFallbackReason(StrEnum):
    """降级原因（写入 ``degraded_reason``，用于排查与告警）。"""

    #: 本地模型不可用（连不上 / 超时 / 返回错误），回退云端
    LOCAL_UNAVAILABLE = "LOCAL_UNAVAILABLE"
    #: 目标档位没有配置模型，回退到更低档位
    TIER_MODEL_UNCONFIGURED = "TIER_MODEL_UNCONFIGURED"
    #: 供应商不可用，回退到另一供应商（双通道）
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class TierRoute:
    """一次调用的路由结果。

    ``tier`` 是**实际生效**的档位——回退后它会低于请求的档位。
    ``requested_tier`` 保留原始请求的档位，两者不一致即表示发生了降级。
    """

    #: 实际生效的档位
    tier: ModelTier
    #: 原始请求的档位
    requested_tier: ModelTier
    #: 使用的供应商（cloud / local）
    provider: str
    #: 使用的模型 ID
    model_id: str
    #: 基础 URL
    base_url: str
    #: API Key（可能为 None——本地 Ollama 默认无鉴权）
    api_key: str | None
    #: 是否发生了降级
    degraded: bool = False
    #: 降级原因（仅 degraded=True 时有值）
    degraded_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """转成可写入 JSON 的形式（**不含 api_key**）。"""
        return {
            "tier": str(self.tier),
            "requested_tier": str(self.requested_tier),
            "provider": self.provider,
            "model_id": self.model_id,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
        }


def model_for_tier(settings: Settings, tier: ModelTier) -> str | None:
    """取出某档位配置的模型 ID；未配置返回 ``None``。"""
    if tier is ModelTier.TIER_LIGHT:
        return settings.LLM_TIER_LIGHT_MODEL
    if tier is ModelTier.TIER_DEEP:
        return settings.LLM_TIER_DEEP_MODEL
    return settings.LLM_TIER_STANDARD_MODEL


def tier_order(tier: ModelTier) -> int:
    """档位的降级顺序（数值越大档位越高）。"""
    if tier is ModelTier.TIER_DEEP:
        return 2
    if tier is ModelTier.TIER_STANDARD:
        return 1
    return 0


def fallback_tiers(tier: ModelTier) -> list[ModelTier]:
    """某档位不可用时，允许降级到的档位列表（从高到低）。

    **DEEP 不降级到 LIGHT**——DEEP 的价值在于准确率，
    拿 LIGHT 档的模型冒充 DEEP 的结果，比直接失败更糟：
    读者会以为这是经过深思熟虑的结论（PRD 24.1 的明确要求：
    「不要降级这一档」）。

    - ``TIER_DEEP`` → ``[TIER_STANDARD]``（只降一档，且不是 LIGHT）
    - ``TIER_STANDARD`` → ``[TIER_LIGHT]``
    - ``TIER_LIGHT`` → ``[]``（已经是最低档，无处可退）
    """
    if tier is ModelTier.TIER_DEEP:
        return [ModelTier.TIER_STANDARD]
    if tier is ModelTier.TIER_STANDARD:
        return [ModelTier.TIER_LIGHT]
    return []


def resolve_tier_route(
    settings: Settings,
    *,
    requested_tier: ModelTier,
    prefer_local: bool = False,
) -> TierRoute:
    """解析本次调用应该用的（供应商, 模型, base_url, api_key）。

    **解析顺序**：

    1. **本地优先**（``prefer_local=True``）：本地模型可用就用本地——
       本地调用不花钱，这是成本优化 R3 的关键。若本地未配置模型，
       直接回退云端（并标注降级）。
    2. **档位未配置模型**：按 ``fallback_tiers`` 逐级降级，
       每一级都标注降级原因。**绝不静默用一个别的档位**。
    3. **全部未配置**：抛 ``LLM_UNAVAILABLE``——这里不能降级到
       「随便找个模型」，静默用错档位比失败更危险。

    **注意本地优先的降级标注**：本地未配置模型时回退云端，
    档位**不变**（仍然是请求的档位），只是供应商变了。
    这与「档位降级」是两回事——``degraded_reason`` 会区分开。
    """
    # ---- 1. 本地优先 ----
    if prefer_local:
        local_model = settings.LLM_PROVIDER_LOCAL_MODEL
        if local_model:
            return TierRoute(
                tier=requested_tier,
                requested_tier=requested_tier,
                provider="local",
                model_id=local_model,
                base_url=settings.LLM_PROVIDER_LOCAL_BASE_URL,
                api_key=(
                    settings.LLM_PROVIDER_LOCAL_API_KEY.get_secret_value()
                    if settings.LLM_PROVIDER_LOCAL_API_KEY
                    else None
                ),
            )
        # 本地没配模型 -> 回退云端，档位不变
        cloud = _cloud_route(settings, requested_tier)
        return TierRoute(
            tier=cloud.tier,
            requested_tier=requested_tier,
            provider=cloud.provider,
            model_id=cloud.model_id,
            base_url=cloud.base_url,
            api_key=cloud.api_key,
            degraded=True,
            degraded_reason=str(TierFallbackReason.TIER_MODEL_UNCONFIGURED),
        )

    # ---- 2. 云端 + 档位降级链 ----
    candidates = [requested_tier, *fallback_tiers(requested_tier)]
    last_reason: str | None = None
    for index, tier in enumerate(candidates):
        model = model_for_tier(settings, tier)
        if not model:
            last_reason = str(TierFallbackReason.TIER_MODEL_UNCONFIGURED)
            continue
        route = _cloud_route(settings, tier, model=model)
        if index == 0:
            return route
        return TierRoute(
            tier=tier,
            requested_tier=requested_tier,
            provider=route.provider,
            model_id=route.model_id,
            base_url=route.base_url,
            api_key=route.api_key,
            degraded=True,
            degraded_reason=last_reason or str(TierFallbackReason.TIER_MODEL_UNCONFIGURED),
        )
    # 全部档位都没配模型 -> 不能随便找个模型用
    raise AICryptoError(
        ErrorCode.LLM_UNAVAILABLE,
        message="所有档位均未配置模型",
        details={
            "requested_tier": str(requested_tier),
            "candidates": [str(t) for t in candidates],
        },
    )


def _cloud_route(settings: Settings, tier: ModelTier, *, model: str | None = None) -> TierRoute:
    """构造云端路由。"""
    model_id = model or model_for_tier(settings, tier) or ""
    return TierRoute(
        tier=tier,
        requested_tier=tier,
        provider="cloud",
        model_id=model_id,
        base_url=settings.LLM_CLOUD_BASE_URL or "",
        api_key=(
            settings.LLM_CLOUD_API_KEY.get_secret_value()
            if settings.LLM_CLOUD_API_KEY
            else None
        ),
    )


def tier_for_trigger(trigger: str, *, retrospective: bool = False) -> ModelTier:
    """按分析触发来源决定档位。

    规则（PRD 24.1、24.3 与成本文档 §5.1）：

    - ``MANUAL``：用户手动发起——**STANDARD**。手动请求是人在等结果，
      用 LIGHT 档会明显降低质量。
    - ``SCHEDULED``：定时分析——**STANDARD**（这是主分析路径）。
    - ``ANOMALY``：异动触发——**STANDARD**。异动解释需要准确的 Bias
      与概率；成本文档 §5.1 里异动的 STANDARD 部分正是
      「1 × LIGHT + 1 × STANDARD」，即异动本身要 STANDARD。
    - ``RETROSPECTIVE``：复盘 / 日报 / 分歧复盘——**DEEP**。
      PRD 24.3 明确「日报与分歧复盘使用 DEEP」。

    **参数 ``retrospective=True`` 优先于 trigger**——它是显式意图，
    比从 trigger 字符串里猜更可靠。
    """
    if retrospective:
        return ModelTier.TIER_DEEP
    normalized = (trigger or "").strip().upper()
    if normalized in {"RETROSPECTIVE", "DAILY", "WEEKLY", "REVIEW", "DEEP"}:
        return ModelTier.TIER_DEEP
    return ModelTier.TIER_STANDARD


__all__ = [
    "ModelTier",
    "TierFallbackReason",
    "TierRoute",
    "fallback_tiers",
    "model_for_tier",
    "resolve_tier_route",
    "tier_for_trigger",
    "tier_order",
]
