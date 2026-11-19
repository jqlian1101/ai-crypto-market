"""增加 AI 综合分析报告与反馈表。

Revision ID: 0002_analysis_reports
Revises: 0001_initial_core_tables
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_analysis_reports"
down_revision: str | None = "0001_initial_core_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "market_snapshots",
        sa.Column("available_as_of", sa.DateTime(timezone=True), nullable=True),
    )
    # 旧快照没有可用时间证明，保留 NULL。

    op.create_table(
        "analysis_runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("analysis_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("market_type", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_analysis_runs_idempotency"),
        sa.Column("status", sa.Text(), nullable=False, server_default="QUEUED"),
        sa.Column("analysis_mode", sa.Text(), nullable=False, server_default="LIVE"),
        sa.Column("market_snapshot_id", sa.Text(), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_id", name="pk_analysis_runs"),
        sa.UniqueConstraint("analysis_id", name="uq_analysis_runs_analysis"),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','COMPLETE','PARTIAL','FAILED','TIMEOUT')",
            name="ck_analysis_runs_status",
        ),
        sa.CheckConstraint(
            "analysis_mode IN ('LIVE','BACKFILL','RETROSPECTIVE')",
            name="ck_analysis_runs_mode",
        ),
    )
    op.create_index(
        "idx_analysis_runs_created",
        "analysis_runs",
        ["symbol", "market_type", sa.text("created_at DESC")],
    )
    op.create_index("idx_analysis_runs_status", "analysis_runs", ["status", "created_at"])

    op.create_table(
        "analysis_reports",
        sa.Column("analysis_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("market_type", sa.Text(), nullable=False),
        sa.Column("market_snapshot_id", sa.Text(), nullable=False),
        sa.Column("snapshot_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("analysis_mode", sa.Text(), nullable=False, server_default="LIVE"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("previous_analysis_id", sa.Text(), nullable=True),
        sa.Column("supersedes_id", sa.Text(), nullable=True),
        sa.Column(
            "agent_run_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("degraded_reason", sa.Text(), nullable=True),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("analysis_id", name="pk_analysis_reports"),
        sa.ForeignKeyConstraint(
            ["market_snapshot_id"],
            ["market_snapshots.snapshot_id"],
            name="fk_analysis_reports_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["previous_analysis_id"],
            ["analysis_reports.analysis_id"],
            name="fk_analysis_reports_previous",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["analysis_reports.analysis_id"],
            name="fk_analysis_reports_supersedes",
        ),
        sa.CheckConstraint(
            "analysis_mode IN ('LIVE','BACKFILL','RETROSPECTIVE')",
            name="ck_analysis_reports_mode",
        ),
        sa.CheckConstraint(
            "status IN ('COMPLETE','PARTIAL')",
            name="ck_analysis_reports_status",
        ),
    )
    op.create_index(
        "idx_analysis_reports_latest",
        "analysis_reports",
        ["symbol", "market_type", sa.text("published_at DESC")],
    )
    op.create_index("idx_analysis_reports_status", "analysis_reports", ["status", "created_at"])

    op.create_table(
        "analysis_feedback",
        sa.Column("feedback_id", sa.Text(), nullable=False),
        sa.Column("analysis_id", sa.Text(), nullable=False),
        sa.Column("useful", sa.Boolean(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("review_kind", sa.Text(), nullable=False, server_default="USER"),
        sa.Column("rubric_version", sa.Text(), nullable=True),
        sa.Column("dimension_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reviewer_ref", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("feedback_id", name="pk_analysis_feedback"),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["analysis_reports.analysis_id"],
            name="fk_analysis_feedback_report",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_analysis_feedback_idempotency"),
        sa.CheckConstraint(
            "review_kind IN ('USER','HUMAN_REVIEW','LLM_ASSISTED')",
            name="ck_analysis_feedback_kind",
        ),
    )
    op.create_index(
        "idx_analysis_feedback_analysis", "analysis_feedback", ["analysis_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_analysis_feedback_analysis", table_name="analysis_feedback")
    op.drop_table("analysis_feedback")
    op.drop_index("idx_analysis_runs_status", table_name="analysis_runs")
    op.drop_index("idx_analysis_runs_created", table_name="analysis_runs")
    op.drop_table("analysis_runs")
    op.drop_index("idx_analysis_reports_status", table_name="analysis_reports")
    op.drop_index("idx_analysis_reports_latest", table_name="analysis_reports")
    op.drop_table("analysis_reports")
    op.drop_column("market_snapshots", "available_as_of")
