"""AI 分析运行、报告与反馈 ORM 模型。"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aicrypto.infrastructure.database.base import Base, bool_column, ck, desc_idx, idx, json_column

_MODES = "analysis_mode IN ('LIVE','BACKFILL','RETROSPECTIVE')"


class AnalysisRunRecord(Base):
    __tablename__ = "analysis_runs"

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    analysis_id: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    market_type: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    # 所属调度窗口（P5）；手动触发 / 历史数据为 NULL
    job_run_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("job_runs.job_run_id", name="fk_analysis_runs_job_run")
    )
    # 触发本次分析的异动事件（PRD 22.6）；非异动触发为 NULL
    anomaly_event_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("anomaly_events.anomaly_event_id", name="fk_analysis_runs_anomaly_event")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="QUEUED")
    analysis_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="LIVE")
    market_snapshot_id: Mapped[str | None] = mapped_column(Text)
    error: Mapped[Any] = json_column(nullable=True)
    started_at: Mapped[dt.datetime | None]
    finished_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime]
    __table_args__ = (
        UniqueConstraint("analysis_id", name="uq_analysis_runs_analysis"),
        UniqueConstraint("idempotency_key", name="uq_analysis_runs_idempotency"),
        ck("ck_analysis_runs_mode", _MODES),
        ck(
            "ck_analysis_runs_status",
            "status IN ('QUEUED','RUNNING','COMPLETE','PARTIAL','FAILED','TIMEOUT')",
        ),
        desc_idx("idx_analysis_runs_created", "symbol", "market_type", "created_at"),
        idx("idx_analysis_runs_status", "status", "created_at"),
    )


class AnalysisReportRecord(Base):
    __tablename__ = "analysis_reports"

    analysis_id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    market_type: Mapped[str] = mapped_column(Text, nullable=False)
    market_snapshot_id: Mapped[str] = mapped_column(
        Text, ForeignKey("market_snapshots.snapshot_id", name="fk_analysis_reports_snapshot")
    )
    snapshot_as_of: Mapped[dt.datetime]
    available_as_of: Mapped[dt.datetime | None]
    published_at: Mapped[dt.datetime | None]
    analysis_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="LIVE")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[Any] = json_column()
    previous_analysis_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("analysis_reports.analysis_id", name="fk_analysis_reports_previous")
    )
    supersedes_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("analysis_reports.analysis_id", name="fk_analysis_reports_supersedes")
    )
    agent_run_ids: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    versions: Mapped[Any] = json_column()
    degraded: Mapped[bool] = bool_column()
    degraded_reason: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1")
    created_at: Mapped[dt.datetime]
    __table_args__ = (
        ck("ck_analysis_reports_mode", _MODES),
        ck("ck_analysis_reports_status", "status IN ('COMPLETE','PARTIAL')"),
        desc_idx("idx_analysis_reports_latest", "symbol", "market_type", "published_at"),
        idx("idx_analysis_reports_status", "status", "created_at"),
    )


class AnalysisFeedbackRecord(Base):
    __tablename__ = "analysis_feedback"

    feedback_id: Mapped[str] = mapped_column(Text, primary_key=True)
    analysis_id: Mapped[str] = mapped_column(
        Text, ForeignKey("analysis_reports.analysis_id", name="fk_analysis_feedback_report")
    )
    useful: Mapped[bool | None]
    reason: Mapped[str | None] = mapped_column(Text)
    review_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="USER")
    rubric_version: Mapped[str | None] = mapped_column(Text)
    dimension_scores: Mapped[Any] = json_column(nullable=True)
    reviewer_ref: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime]
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_analysis_feedback_idempotency"),
        ck("ck_analysis_feedback_kind", "review_kind IN ('USER','HUMAN_REVIEW','LLM_ASSISTED')"),
        idx("idx_analysis_feedback_analysis", "analysis_id", "created_at"),
    )
