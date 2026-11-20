"""V0005: 通知推送表（notification_events、notification_dead_letters、notification_preferences）。

PRD 第 30.7 节。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_notification_tables"
down_revision: str | None = "0004_anomaly_rule_fk"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # notification_events 表
    op.create_table(
        "notification_events",
        sa.Column("notification_event_id", sa.Text, nullable=False, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("push_mode", sa.Text, nullable=False),
        sa.Column("fingerprint", sa.Text, nullable=True),
        sa.Column("revision", sa.Integer, nullable=False, server_default="0"),
        sa.Column("revision_reason", sa.Text, nullable=True),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("rendered_type", sa.Text, nullable=False),
        sa.Column("recipient_summary", sa.Text, nullable=True),
        sa.Column("payload_digest", sa.Text, nullable=False),
        sa.Column("payload_bytes", sa.Integer, nullable=False),
        sa.Column("delivery_status", sa.Text, nullable=False, server_default="PENDING"),
        sa.Column("response_code", sa.Text, nullable=True),
        sa.Column("response_body", postgresql.JSONB, nullable=True),
        sa.Column("invalid_recipients", postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("is_test", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_backfill", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("error", postgresql.JSONB, nullable=True),
        sa.Column("anomaly_event_id", sa.Text, nullable=True),
        sa.Column("signal_id", sa.Text, nullable=True),
        sa.Column("job_run_id", sa.Text, nullable=True),
        sa.CheckConstraint(
            "severity IN ('INFO','WARN','CRITICAL')",
            name="notif_events_severity",
        ),
        sa.CheckConstraint(
            "delivery_status IN ('SUCCESS','PARTIAL','FAILED','RATE_LIMITED','SKIPPED','PENDING')",
            name="notif_events_status",
        ),
        sa.ForeignKeyConstraint(
            ["anomaly_event_id"],
            ["anomaly_events.anomaly_event_id"],
            name="fk_notif_events_anomaly",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "idx_notification_events_created",
        "notification_events",
        [sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_notification_events_status",
        "notification_events",
        ["delivery_status", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_notification_events_fingerprint",
        "notification_events",
        ["fingerprint", "revision"],
    )

    # notification_dead_letters 表
    op.create_table(
        "notification_dead_letters",
        sa.Column("dead_letter_id", sa.Text, nullable=False, primary_key=True),
        sa.Column("notification_event_id", sa.Text, nullable=False),
        sa.Column("channel", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("payload_ref", sa.Text, nullable=False),
        sa.Column("retryable", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text, nullable=True),
        sa.ForeignKeyConstraint(
            ["notification_event_id"],
            ["notification_events.notification_event_id"],
            name="fk_dead_letter_event",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_notification_dead_letters_created",
        "notification_dead_letters",
        [sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_notification_dead_letters_event",
        "notification_dead_letters",
        ["notification_event_id"],
    )

    # notification_preferences 表
    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Text, nullable=False, primary_key=True),
        sa.Column("daily_quota", sa.Integer, nullable=False, server_default="60"),
        sa.Column("quiet_hours", sa.Text, nullable=False, server_default="00:00-07:00"),
        sa.Column("critical_bypass_quiet", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "channel_enabled", postgresql.JSONB, nullable=False, server_default='{"feishu": true}'
        ),
        sa.Column("delivery_mode", sa.Text, nullable=False, server_default="broadcast"),
        sa.Column("rules", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "delivery_mode IN ('broadcast','first_success','severity_routing')",
            name="notif_prefs_mode",
        ),
    )


def downgrade() -> None:
    op.drop_table("notification_preferences")
    op.drop_table("notification_dead_letters")
    op.drop_table("notification_events")
