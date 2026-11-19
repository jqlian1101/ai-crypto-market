"""AI 分析报告领域对象（PRD 4.7.1 / v2.4）。"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

REPORT_SCHEMA_VERSION = "1"


class AnalysisMode(StrEnum):
    LIVE = "LIVE"
    BACKFILL = "BACKFILL"
    RETROSPECTIVE = "RETROSPECTIVE"


class AnalysisRunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class EvidenceRef(BaseModel):
    """可定位到冻结快照或工具结果的证据引用。"""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source_type: str
    source_path: str
    value: str | int | float | bool | None = None
    unit: str | None = None
    observed_at: dt.datetime | None = None
    available_at: dt.datetime | None = None
    snapshot_id: str
    data_quality: str | None = None
    interpretation: str


class ScenarioProbability(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    up: float = Field(ge=0, le=1)
    down: float = Field(ge=0, le=1)
    flat: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def probabilities_sum_to_one(self) -> ScenarioProbability:
        if abs(self.up + self.down + self.flat - 1.0) > 1e-6:
            raise ValueError("scenario_probabilities 必须总和为 1")
        return self


class AnalysisScenario(BaseModel):
    name: str
    probability: float | None = Field(default=None, ge=0, le=1)
    time_horizon: str
    description: str
    trigger_conditions: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)


class AnalysisReportContent(BaseModel):
    """面向用户的综合报告内容；不是 Agent 输出的拼接结果。"""

    model_config = ConfigDict(extra="forbid")

    summary: str
    market_bias: str
    time_horizon: str
    dominant_factors: list[str] = Field(default_factory=list)
    timeframe_views: dict[str, str] = Field(default_factory=dict)
    scenarios: list[AnalysisScenario] = Field(default_factory=list)
    scenario_probabilities: ScenarioProbability | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    counter_evidence: list[EvidenceRef] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    key_levels: list[str] = Field(default_factory=list)
    watch_conditions: list[str] = Field(default_factory=list)
    changes_since_previous: str | None = None
    uncertainties: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    prediction_ids: list[str] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    """不可变的用户可读 AI 综合分析报告。"""

    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    symbol: str
    market_type: str
    market_snapshot_id: str
    snapshot_as_of: dt.datetime
    available_as_of: dt.datetime | None = None
    published_at: dt.datetime | None = None
    analysis_mode: AnalysisMode = AnalysisMode.LIVE
    status: AnalysisRunStatus
    content: AnalysisReportContent
    previous_analysis_id: str | None = None
    supersedes_id: str | None = None
    agent_run_ids: list[str] = Field(default_factory=list)
    versions: dict[str, str] = Field(default_factory=dict)
    degraded: bool = False
    degraded_reason: str | None = None
    schema_version: str = REPORT_SCHEMA_VERSION
    created_at: dt.datetime


class AnalysisRun(BaseModel):
    """一次分析流程的持久化状态。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    analysis_id: str
    symbol: str
    market_type: str
    trigger: str
    status: AnalysisRunStatus = AnalysisRunStatus.QUEUED
    analysis_mode: AnalysisMode = AnalysisMode.LIVE
    market_snapshot_id: str | None = None
    error: dict[str, object] | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    created_at: dt.datetime


__all__ = [
    "AnalysisMode",
    "AnalysisReport",
    "AnalysisReportContent",
    "AnalysisRun",
    "AnalysisRunStatus",
    "AnalysisScenario",
    "EvidenceRef",
    "ScenarioProbability",
]
