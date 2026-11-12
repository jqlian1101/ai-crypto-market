"""AI 与研究领域模型（PRD 第 30.4 节）。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from aicrypto.infrastructure.database.base import (
    Base,
    JsonType,
    PriceType,
    RatioType,
    bool_column,
    ck,
    idx,
    json_column,
    ts_column,
)


class AgentRun(Base):
    """一次 Agent 运行。"""

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
        idx("agent_runs", "analysis_id"),
        idx("agent_runs", "agent_name", "started_at"),
        idx("agent_runs", "market_snapshot_id"),
        ck(
            "agent_runs_status",
            "status IN ('RUNNING','SUCCESS','FAILED','TIMEOUT','SKIPPED_DEGRADED')",
        ),
        ck("agent_runs_tier", "tier IS NULL OR tier IN ('TIER_LIGHT','TIER_STANDARD','TIER_DEEP')"),
    )


class AgentOutput(Base):
    """Agent 的结构化输出。

    ``output`` 用 JSONB 而非拆列：字段会随 Agent 演化。
    **但 schema 必须版本化**（``schema_version``），且读取时必须做版本适配。
    """

    __tablename__ = "agent_outputs"

    output_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    output: Mapped[Any] = json_column()
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)

    __table_args__ = (
        Index("fk_agent_outputs_run_id", "run_id", unique=False),
        idx("agent_outputs", "run_id"),
        idx("agent_outputs", "created_at"),
    )


class ModelRun(Base):
    """一次模型调用。"""

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
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[Any] = json_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)

    __table_args__ = (
        idx("model_runs", "agent_run_id"),
        idx("model_runs", "model_id", "created_at"),
        ck(
            "model_runs_status",
            "status IN ('SUCCESS','FAILED','TIMEOUT','SCHEMA_INVALID','RATE_LIMITED')",
        ),
    )


class LLMCostRecord(Base):
    """LLM 成本记录。

    ``price_snapshot`` 保存**计算时使用的单价快照**，
    否则价格变动会让成本报表失真（DATA_AND_COST_ANALYSIS §5 缺口 2）。
    """

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
        idx("llm_cost_records", "created_at"),
        idx("llm_cost_records", "model_id", "created_at"),
    )


class PromptVersion(Base):
    """Prompt 版本登记。

    ``template_hash`` 用于确认线上跑的到底是哪份模板。
    """

    __tablename__ = "prompt_versions"

    agent_name: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    template_path: Mapped[str] = mapped_column(Text, nullable=False)
    template_hash: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    change_note: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (idx("prompt_versions", "created_at"),)


class AgentVersion(Base):
    """Agent 版本登记。``tool_allowlist`` 是最小权限的载体（34.8）。"""

    __tablename__ = "agent_versions"

    agent_name: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    agent_version: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    config: Mapped[Any] = json_column()
    tool_allowlist: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    tier: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)

    __table_args__ = (ck("agent_versions_tier", "tier IN ('TIER_LIGHT','TIER_STANDARD','TIER_DEEP')"),)


class MarketSnapshotRecord(Base):
    """市场快照落库（30.3）。

    与 :class:`aicrypto.domain.analysis.snapshot.MarketSnapshot` 领域对象对应。
    """

    __tablename__ = "market_snapshots"

    snapshot_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    market_type: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[dt.datetime] = mapped_column(nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    time_range_start: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    time_range_end: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    data_quality: Mapped[str] = mapped_column(Text, nullable=False)
    data_quality_detail: Mapped[Any] = json_column()
    feature_version: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[Any] = json_column()
    source_watermarks: Mapped[Any] = json_column()

    __table_args__ = (idx("market_snapshots", "symbol", "market_type", "as_of"),)


__all__ = [
    "AgentOutput",
    "AgentRun",
    "AgentVersion",
    "LLMCostRecord",
    "MarketSnapshotRecord",
    "ModelRun",
    "PromptVersion",
]
