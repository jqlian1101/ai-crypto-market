"""初始表结构：行情数据 + 特征快照 + AI 运行 + 系统审计。

对应 PRD 第 30.2 / 30.3 / 30.4 / 30.7 节。

Revision ID: 0001_initial_core_tables
Revises:
Create Date: 2026-11-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_core_tables"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建立 Phase 0 所需的核心表。"""
    # ------------------------------------------------------------------
    # 30.2 行情数据
    # ------------------------------------------------------------------
    op.create_table(
        "market_candles",
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("market_type", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(38, 12), nullable=False),
        sa.Column("high", sa.Numeric(38, 12), nullable=False),
        sa.Column("low", sa.Numeric(38, 12), nullable=False),
        sa.Column("close", sa.Numeric(38, 12), nullable=False),
        sa.Column("volume", sa.Numeric(38, 12), nullable=False),
        sa.Column("quote_volume", sa.Numeric(38, 12), nullable=False),
        sa.Column("trades_count", sa.Integer(), nullable=False),
        sa.Column("taker_buy_base_volume", sa.Numeric(38, 12), nullable=True),
        sa.Column("taker_buy_quote_volume", sa.Numeric(38, 12), nullable=True),
        sa.Column("exchange_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "exchange", "market_type", "symbol", "timeframe", "open_time",
            name="pk_market_candles",
        ),
        sa.CheckConstraint(
            "market_type IN ('SPOT','USDM_FUTURES')", name="ck_market_candles_market_type"
        ),
        sa.CheckConstraint(
            "timeframe IN ('1m','5m','15m','1h','4h')", name="ck_market_candles_timeframe"
        ),
        sa.CheckConstraint(
            "source IN ('WS','REST','REST_BACKFILL')", name="ck_market_candles_source"
        ),
        sa.CheckConstraint(
            "high >= greatest(open, close, low) AND high >= low",
            name="ck_market_candles_ohlc",
        ),
        sa.CheckConstraint(
            "volume >= 0 AND quote_volume >= 0", name="ck_market_candles_volume"
        ),
    )
    op.create_index(
        "idx_candles_lookup",
        "market_candles",
        ["symbol", "market_type", "timeframe", sa.text("close_time DESC")],
    )
    op.create_index(
        "idx_candles_processed", "market_candles", ["processed_at"]
    )

    op.create_table(
        "market_trades",
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("market_type", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("trade_id", sa.BigInteger(), nullable=False),
        sa.Column("price", sa.Numeric(38, 12), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("quote_quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("is_taker_buy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exchange_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "exchange", "market_type", "symbol", "trade_id", name="pk_market_trades"
        ),
        sa.CheckConstraint(
            "market_type IN ('SPOT','USDM_FUTURES')", name="ck_market_trades_market_type"
        ),
    )
    op.create_index(
        "idx_trades_window",
        "market_trades",
        ["symbol", "market_type", sa.text("exchange_ts DESC")],
    )

    op.create_table(
        "orderbook_snapshots",
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("market_type", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exchange_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("asks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("best_bid", sa.Numeric(38, 12), nullable=False),
        sa.Column("best_ask", sa.Numeric(38, 12), nullable=False),
        sa.Column("bid_qty_sum", sa.Numeric(38, 12), nullable=False),
        sa.Column("ask_qty_sum", sa.Numeric(38, 12), nullable=False),
        sa.Column("spread_bps", sa.Numeric(12, 4), nullable=False),
        sa.PrimaryKeyConstraint(
            "exchange", "market_type", "symbol", "captured_at",
            name="pk_orderbook_snapshots",
        ),
    )
    op.create_index(
        "idx_books_symbol",
        "orderbook_snapshots",
        ["symbol", "market_type", sa.text("captured_at DESC")],
    )

    op.create_table(
        "open_interest_history",
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("market_type", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_interest", sa.Numeric(38, 12), nullable=False),
        sa.Column("open_interest_value", sa.Numeric(38, 12), nullable=True),
        sa.PrimaryKeyConstraint(
            "exchange", "market_type", "symbol", "ts", name="pk_open_interest_history"
        ),
    )
    op.create_index(
        "idx_oi_lookup",
        "open_interest_history",
        ["symbol", "market_type", sa.text("ts DESC")],
    )

    op.create_table(
        "funding_history",
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("market_type", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("funding_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("funding_rate", sa.Numeric(18, 10), nullable=False),
        sa.Column("mark_price", sa.Numeric(38, 12), nullable=False),
        sa.PrimaryKeyConstraint(
            "exchange", "market_type", "symbol", "funding_time", name="pk_funding_history"
        ),
    )
    op.create_index(
        "idx_funding_lookup",
        "funding_history",
        ["symbol", "market_type", sa.text("funding_time DESC")],
    )

    op.create_table(
        "instrument_metadata",
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("market_type", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("price_precision", sa.SmallInteger(), nullable=False),
        sa.Column("quantity_precision", sa.SmallInteger(), nullable=False),
        sa.Column("tick_size", sa.Numeric(38, 12), nullable=False),
        sa.Column("step_size", sa.Numeric(38, 12), nullable=False),
        sa.Column("min_qty", sa.Numeric(38, 12), nullable=False),
        sa.Column("min_notional", sa.Numeric(38, 12), nullable=False),
        sa.Column(
            "contract_multiplier", sa.Numeric(38, 12), nullable=False, server_default="1"
        ),
        sa.Column("quote_asset", sa.Text(), nullable=False),
        sa.Column("margin_asset", sa.Text(), nullable=True),
        sa.Column("onboard_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint(
            "exchange", "market_type", "symbol", name="pk_instrument_metadata"
        ),
    )

    op.create_table(
        "market_data_quality_events",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("market_type", sa.Text(), nullable=True),
        sa.Column("symbol", sa.Text(), nullable=True),
        sa.Column("timeframe", sa.Text(), nullable=True),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("data_quality", sa.Text(), nullable=False),
        sa.Column("detected_by", sa.Text(), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_market_data_quality_events"),
        sa.CheckConstraint(
            "severity IN ('INFO','WARN','CRITICAL')",
            name="ck_market_data_quality_events_severity",
        ),
        sa.CheckConstraint(
            "data_quality IN ('GOOD','DEGRADED','STALE','MISSING','INVALID')",
            name="ck_market_data_quality_events_quality",
        ),
        sa.CheckConstraint(
            "detected_by IN ('WS','REST','CROSSCHECK','SCHEDULER','SYSTEM')",
            name="ck_market_data_quality_events_detected_by",
        ),
    )
    op.create_index(
        "idx_quality_events_detected", "market_data_quality_events", ["detected_at"]
    )
    op.create_index(
        "idx_quality_events_symbol", "market_data_quality_events", ["symbol", "rule_id"]
    )

    op.create_table(
        "market_data_sync_runs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exchange", sa.Text(), nullable=False, server_default="binance"),
        sa.Column("market_type", sa.Text(), nullable=True),
        sa.Column("symbol", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("candles_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gaps_detected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_market_data_sync_runs"),
        sa.CheckConstraint(
            "status IN ('RUNNING','SUCCESS','FAILED','PARTIAL')",
            name="ck_market_data_sync_runs_status",
        ),
    )
    op.create_index("idx_sync_runs_started", "market_data_sync_runs", ["started_at"])

    # ------------------------------------------------------------------
    # 30.3 特征与快照
    # ------------------------------------------------------------------
    op.create_table(
        "market_snapshots",
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("market_type", sa.Text(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_range_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_range_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_quality", sa.Text(), nullable=False),
        sa.Column(
            "data_quality_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("feature_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "source_watermarks", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.PrimaryKeyConstraint("snapshot_id", name="pk_market_snapshots"),
    )
    op.create_index(
        "idx_snapshots_lookup",
        "market_snapshots",
        ["symbol", "market_type", sa.text("as_of DESC")],
    )

    # ------------------------------------------------------------------
    # 30.4 AI 与研究
    # ------------------------------------------------------------------
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("analysis_id", sa.Text(), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("agent_version", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("market_snapshot_id", sa.Text(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_horizon", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=True),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("degraded_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("run_id", name="pk_agent_runs"),
        sa.CheckConstraint(
            "status IN ('RUNNING','SUCCESS','FAILED','TIMEOUT','SKIPPED_DEGRADED')",
            name="ck_agent_runs_status",
        ),
        sa.CheckConstraint(
            "tier IS NULL OR tier IN ('TIER_LIGHT','TIER_STANDARD','TIER_DEEP')",
            name="ck_agent_runs_tier",
        ),
    )
    op.create_index("idx_agent_runs_analysis", "agent_runs", ["analysis_id"])
    op.create_index("idx_agent_runs_agent", "agent_runs", ["agent_name", "started_at"])
    op.create_index("idx_agent_runs_snapshot", "agent_runs", ["market_snapshot_id"])

    op.create_table(
        "agent_outputs",
        sa.Column("output_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("output_id", name="pk_agent_outputs"),
    )
    op.create_index("idx_agent_outputs_run", "agent_outputs", ["run_id"])
    op.create_index("idx_agent_outputs_created", "agent_outputs", ["created_at"])

    op.create_table(
        "model_runs",
        sa.Column("model_run_id", sa.Text(), nullable=False),
        sa.Column("agent_run_id", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("model_run_id", name="pk_model_runs"),
        sa.CheckConstraint(
            "status IN ('SUCCESS','FAILED','TIMEOUT','SCHEMA_INVALID','RATE_LIMITED')",
            name="ck_model_runs_status",
        ),
    )
    op.create_index("idx_model_runs_agent", "model_runs", ["agent_run_id"])
    op.create_index(
        "idx_model_runs_model", "model_runs", ["model_id", "created_at"]
    )

    op.create_table(
        "llm_cost_records",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_run_id", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=True),
        sa.Column("tier", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column(
            "price_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("is_degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id", name="pk_llm_cost_records"),
    )
    op.create_index("idx_llm_cost_created", "llm_cost_records", ["created_at"])
    op.create_index(
        "idx_llm_cost_model", "llm_cost_records", ["model_id", "created_at"]
    )

    op.create_table(
        "prompt_versions",
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("template_path", sa.Text(), nullable=False),
        sa.Column("template_hash", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("agent_name", "prompt_version", name="pk_prompt_versions"),
    )
    op.create_index("idx_prompt_versions_created", "prompt_versions", ["created_at"])

    op.create_table(
        "agent_versions",
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("agent_version", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "tool_allowlist", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("agent_name", "agent_version", name="pk_agent_versions"),
        sa.CheckConstraint(
            "tier IN ('TIER_LIGHT','TIER_STANDARD','TIER_DEEP')",
            name="ck_agent_versions_tier",
        ),
    )

    # ------------------------------------------------------------------
    # 30.7 调度与系统
    # ------------------------------------------------------------------
    op.create_table(
        "config_versions",
        sa.Column("version_id", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("app_version", sa.Text(), nullable=False),
        sa.Column("migration_revision", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("version_id", name="pk_config_versions"),
    )
    op.create_index("idx_config_versions_applied", "config_versions", ["applied_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index("idx_audit_logs_occurred", "audit_logs", ["occurred_at"])
    op.create_index("idx_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"])

    op.create_table(
        "system_events",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_system_events"),
        sa.CheckConstraint(
            "severity IN ('INFO','WARN','CRITICAL')", name="ck_system_events_severity"
        ),
    )
    op.create_index("idx_system_events_occurred", "system_events", ["occurred_at"])

    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("invoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_tool_invocations"),
    )
    op.create_index(
        "idx_tool_invocations_agent", "tool_invocations", ["agent_name", "invoked_at"]
    )


def downgrade() -> None:
    """回滚初始表结构。"""
    for table in (
        "tool_invocations",
        "system_events",
        "audit_logs",
        "config_versions",
        "agent_versions",
        "prompt_versions",
        "llm_cost_records",
        "model_runs",
        "agent_outputs",
        "agent_runs",
        "market_snapshots",
        "market_data_sync_runs",
        "market_data_quality_events",
        "instrument_metadata",
        "funding_history",
        "open_interest_history",
        "orderbook_snapshots",
        "market_trades",
        "market_candles",
    ):
        op.drop_table(table)
