"""AI、研究与快照持久化模型（PRD 30.3 / 30.4）。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from aicrypto.infrastructure.database.base import Base, bool_column, ck, desc_idx, idx, json_column


class AgentRun(Base):
    __tablename__ = "agent_runs"
    run_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    analysis_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    agent_version: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    market_snapshot_id: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[dt.datetime] = mapped_column(nullable=False)
    time_horizon: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    degraded: Mapped[bool] = bool_column(default=False)
    degraded_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_ref: Mapped[Any] = json_column()
    error: Mapped[Any] = json_column(nullable=True)
    __table_args__ = (
        idx("idx_agent_runs_analysis", "analysis_id"),
        idx("idx_agent_runs_agent", "agent_name", "started_at"),
        idx("idx_agent_runs_snapshot", "market_snapshot_id"),
        ck(
            "agent_runs_status",
            "status IN ('RUNNING','SUCCESS','FAILED','TIMEOUT','SKIPPED_DEGRADED')",
        ),
        ck("agent_runs_tier", "tier IS NULL OR tier IN ('TIER_LIGHT','TIER_STANDARD','TIER_DEEP')"),
    )


class AgentOutput(Base):
    __tablename__ = "agent_outputs"
    output_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    output: Mapped[Any] = json_column()
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    __table_args__ = (
        idx("idx_agent_outputs_run", "run_id"),
        idx("idx_agent_outputs_created", "created_at"),
    )


class ModelRun(Base):
    __tablename__ = "model_runs"
    model_run_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    agent_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cached_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[Any] = json_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    __table_args__ = (
        idx("idx_model_runs_agent", "agent_run_id"),
        idx("idx_model_runs_model", "model_id", "created_at"),
        ck(
            "model_runs_status",
            "status IN ('SUCCESS','FAILED','TIMEOUT','SCHEMA_INVALID','RATE_LIMITED')",
        ),
    )


class LLMCostRecord(Base):
    __tablename__ = "llm_cost_records"
    id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    model_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(nullable=False)
    price_snapshot: Mapped[Any] = json_column()
    is_degraded: Mapped[bool] = bool_column(default=False)
    __table_args__ = (
        idx("idx_llm_cost_created", "created_at"),
        idx("idx_llm_cost_model", "model_id", "created_at"),
    )


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    agent_name: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    template_path: Mapped[str] = mapped_column(Text, nullable=False)
    template_hash: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    change_note: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (idx("idx_prompt_versions_created", "created_at"),)


class ModelPricing(Base):
    """模型单价表（PRD 30.4 / 24.2）。

    单价以 **USD / 百万 token** 存储（``input_usd_per_mtok`` /
    ``output_usd_per_mtok``）——这是供应商报价的惯例单位，
    直接存「每 token 单价」会引入大量小数位，容易在换算时丢精度。

    **单价版本化**：``pricing_version`` 记录这行单价的版本号。
    改了单价就新增一行（``model_id`` + ``pricing_version`` 唯一），
    历史 ``llm_cost_records.price_snapshot`` 里已经固化了当时的单价，
    因此改价不会让历史成本失真。
    """

    __tablename__ = "model_pricing"
    model_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    pricing_version: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    input_usd_per_mtok: Mapped[Decimal] = mapped_column(nullable=False)
    output_usd_per_mtok: Mapped[Decimal] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="USD")
    effective_from: Mapped[dt.datetime] = mapped_column(nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    __table_args__ = (
        ck("ck_model_pricing_currency", "currency IN ('USD')"),
        ck(
            "ck_model_pricing_non_negative",
            "input_usd_per_mtok >= 0 AND output_usd_per_mtok >= 0",
        ),
        desc_idx("idx_model_pricing_lookup", "model_id", "effective_from"),
    )


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    agent_name: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    agent_version: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    config: Mapped[Any] = json_column()
    tool_allowlist: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    tier: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    __table_args__ = (
        ck("agent_versions_tier", "tier IN ('TIER_LIGHT','TIER_STANDARD','TIER_DEEP')"),
    )


class MarketSnapshotRecord(Base):
    """对应纯领域对象 domain.analysis.snapshot.MarketSnapshot。"""

    __tablename__ = "market_snapshots"
    snapshot_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    market_type: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[dt.datetime] = mapped_column(nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    available_as_of: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    time_range_start: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    time_range_end: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    data_quality: Mapped[str] = mapped_column(Text, nullable=False)
    data_quality_detail: Mapped[Any] = json_column()
    feature_version: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[Any] = json_column()
    source_watermarks: Mapped[Any] = json_column()
    __table_args__ = (desc_idx("idx_snapshots_lookup", "symbol", "market_type", "as_of"),)
