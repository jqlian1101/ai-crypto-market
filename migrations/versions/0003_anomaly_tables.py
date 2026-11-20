"""增加异动规则与异动事件表（PRD 30.6）。

Revision ID: 0003_anomaly_tables
Revises: 0002_analysis_reports
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_anomaly_tables"
down_revision: str | None = "0002_analysis_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "anomaly_rules",
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("push_mode", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("asset_overrides", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("market_type", sa.Text(), nullable=False, server_default="USDM_FUTURES"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("rule_id", "rule_version", name="pk_anomaly_rules"),
        sa.CheckConstraint(
            "severity IN ('INFO','WARN','CRITICAL')", name="ck_anomaly_rules_severity"
        ),
        sa.CheckConstraint(
            "push_mode IN ('PUSH','AGGREGATE','DIGEST')", name="ck_anomaly_rules_push_mode"
        ),
        sa.CheckConstraint(
            "category IN ('PRICE','VOLATILITY','VOLUME','DERIVATIVES','DATA_SYSTEM')",
            name="ck_anomaly_rules_category",
        ),
    )

    op.create_table(
        "anomaly_events",
        sa.Column("anomaly_event_id", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("market_type", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("detected_by", sa.Text(), nullable=False, server_default="RULE_ENGINE"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market_snapshot_id", sa.Text(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("threshold", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("cooldown_key", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "revision_reason",
            sa.Text(),
            nullable=False,
            server_default="INITIAL_DETERMINISTIC",
        ),
        sa.Column("llm_interpretation_id", sa.Text(), nullable=True),
        sa.Column("notification_status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.PrimaryKeyConstraint("anomaly_event_id", name="pk_anomaly_events"),
        sa.UniqueConstraint("fingerprint", "revision", name="uq_anomaly_events_fp_rev"),
        sa.CheckConstraint(
            "severity IN ('INFO','WARN','CRITICAL')", name="ck_anomaly_events_severity"
        ),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('UP','DOWN','NEUTRAL')",
            name="ck_anomaly_events_direction",
        ),
        sa.CheckConstraint(
            "notification_status IN ('PENDING','SENT','SUPPRESSED','FAILED')",
            name="ck_anomaly_events_notification_status",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_anomaly_events_revision"),
    )
    op.create_index(
        "idx_anomaly_events_detected",
        "anomaly_events",
        [sa.text("detected_at DESC")],
    )
    op.create_index("idx_anomaly_events_rule", "anomaly_events", ["rule_id", "detected_at"])


def downgrade() -> None:
    op.drop_index("idx_anomaly_events_rule", table_name="anomaly_events")
    op.drop_index("idx_anomaly_events_detected", table_name="anomaly_events")
    op.drop_table("anomaly_events")
    op.drop_table("anomaly_rules")
