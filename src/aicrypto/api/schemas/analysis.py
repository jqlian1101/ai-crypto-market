"""AI 分析报告 HTTP 契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aicrypto.domain.analysis.report import AnalysisReport, AnalysisRun


class FeedbackSummary(BaseModel):
    count: int
    useful_count: int
    not_useful_count: int


class AnalysisReportSchema(AnalysisReport):
    feedback_summary: FeedbackSummary | None = None


class AnalysisFeedbackRequest(BaseModel):
    useful: bool | None = None
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_feedback(self) -> AnalysisFeedbackRequest:
        if self.useful is None and not (self.reason and self.reason.strip()):
            raise ValueError("请提供评价或反馈原因")
        return self


class AnalysisFeedbackResponse(BaseModel):
    feedback_id: str
    analysis_id: str
    created_at: datetime


class AnalysisRunRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(min_length=1, max_length=32)
    market_type: Literal["SPOT", "USDM_FUTURES"] = "SPOT"
    trigger: Literal["MANUAL", "SCHEDULED", "ANOMALY"] = "MANUAL"


class AnalysisRunSchema(AnalysisRun):
    status_url: str
