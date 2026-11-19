"""Agent 结构化输出契约（PRD 第 4.7 章）。

每个 Agent **必须**输出以下 15 个字段，缺一不可。
``confidence`` 是模型自述的主观置信度，**不等于**统计胜率；
只有经过历史校准后，才能把 ``probability`` 解释为经验概率（33.4）。
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 输出结构版本（30.4：schema 必须版本化）
OUTPUT_SCHEMA_VERSION = "1"


class AgentOutput(BaseModel):
    """Agent 的结构化输出。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # --- 溯源 ---
    agent_name: str = Field(description="Agent 名称")
    agent_version: str = Field(description="Agent 版本")
    model_id: str = Field(description="模型 ID")
    prompt_version: str = Field(description="Prompt 版本")
    market_snapshot_id: str = Field(description="所依据的市场快照")
    as_of: dt.datetime = Field(description="数据截止时刻（交易所时间）")
    time_horizon: str = Field(description="结论适用的时间跨度，如 4h / 1d")

    # --- 结论 ---
    summary: str = Field(description="人类可读摘要")
    findings: list[str] = Field(default_factory=list, description="结构化发现")
    supporting_evidence: list[str] = Field(default_factory=list, description="支持证据")
    contradicting_evidence: list[str] = Field(default_factory=list, description="反证")
    uncertainties: list[str] = Field(default_factory=list, description="不确定性")
    missing_data: list[str] = Field(default_factory=list, description="缺失的数据项")
    confidence: float = Field(ge=0.0, le=1.0, description="主观置信度，不等于统计胜率")
    scenario_probabilities: dict[str, float] | None = Field(
        default=None, description="互斥 UP/DOWN/FLAT 情景概率，供三分类评估"
    )
    invalidation_conditions: list[str] = Field(default_factory=list, description="使结论失效的条件")

    @model_validator(mode="after")
    def validate_scenario_probabilities(self) -> AgentOutput:
        if self.scenario_probabilities is None:
            return self
        expected = {"up", "down", "flat"}
        if set(self.scenario_probabilities) != expected:
            raise ValueError("scenario_probabilities 必须包含 up/down/flat")
        if any(not 0 <= value <= 1 for value in self.scenario_probabilities.values()):
            raise ValueError("情景概率必须位于 [0, 1]")
        if abs(sum(self.scenario_probabilities.values()) - 1.0) > 1e-6:
            raise ValueError("情景概率之和必须为 1")
        return self


class AgentStatus(StrEnum):
    """30.4 的 ``agent_runs.status`` 取值。"""

    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    SKIPPED_DEGRADED = "SKIPPED_DEGRADED"


class ModelTier(StrEnum):
    """24.1 的模型分层。"""

    TIER_LIGHT = "TIER_LIGHT"
    TIER_STANDARD = "TIER_STANDARD"
    TIER_DEEP = "TIER_DEEP"


class Direction(StrEnum):
    """33.2 的方向标签。"""

    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"


class MarketRegime(StrEnum):
    """4.6 / 30.3 的 8 态市场状态。"""

    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT_ATTEMPT = "BREAKOUT_ATTEMPT"
    TREND_REVERSAL = "TREND_REVERSAL"
    UNCERTAIN = "UNCERTAIN"


class VolatilityState(StrEnum):
    """30.3 的 3 态波动状态（与 regime 正交）。"""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class SignalDirection(StrEnum):
    """30.5 的信号方向。"""

    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


def validate_confidence_is_not_probability(output: AgentOutput) -> bool:
    """校验 ``confidence`` 未被误当作概率（36.3 必测清单）。

    规则：只有当输出显式提供 ``probability`` 时才能计算 Brier Score；
    本函数返回是否**可以**计算 Brier。
    """
    extra = output.model_extra or {}
    return output.scenario_probabilities is not None or "probability" in extra


__all__ = [
    "OUTPUT_SCHEMA_VERSION",
    "AgentOutput",
    "AgentStatus",
    "Direction",
    "MarketRegime",
    "ModelTier",
    "SignalDirection",
    "VolatilityState",
    "validate_confidence_is_not_probability",
]
