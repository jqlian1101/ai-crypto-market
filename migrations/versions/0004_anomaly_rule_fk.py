"""为 anomaly_events 增加到 anomaly_rules 的复合外键（PRD 30.6）。

0003 未包含该外键；为不修改已应用的迁移，以新迁移补齐引用完整性。

Revision ID: 0004_anomaly_rule_fk
Revises: 0003_anomaly_tables
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_anomaly_rule_fk"
down_revision: str | None = "0003_anomaly_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_anomaly_events_rule",
        "anomaly_events",
        "anomaly_rules",
        ["rule_id", "rule_version"],
        ["rule_id", "rule_version"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_anomaly_events_rule", "anomaly_events", type_="foreignkey")
