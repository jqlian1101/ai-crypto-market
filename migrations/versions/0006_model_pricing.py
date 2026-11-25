"""V0006: 模型单价表（model_pricing）。

PRD 第 30.4 节。

``llm_cost_records`` 表已存在但**没有任何写入方**——成本分析无从谈起。
本表提供单价真源：改动单价通过新增版本行实现，历史
``llm_cost_records.price_snapshot`` 里已固化的单价不受影响。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_model_pricing"
down_revision: str | None = "0005_notification_tables"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "model_pricing",
        sa.Column("model_id", sa.Text, nullable=False),
        sa.Column("pricing_version", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("input_usd_per_mtok", sa.Numeric(38, 12), nullable=False),
        sa.Column("output_usd_per_mtok", sa.Numeric(38, 12), nullable=False),
        sa.Column("currency", sa.Text, nullable=False, server_default="USD"),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("model_id", "pricing_version", name="pk_model_pricing"),
        sa.CheckConstraint("currency IN ('USD')", name="ck_model_pricing_currency"),
        sa.CheckConstraint(
            "input_usd_per_mtok >= 0 AND output_usd_per_mtok >= 0",
            name="ck_model_pricing_non_negative",
        ),
    )
    op.create_index(
        "idx_model_pricing_lookup",
        "model_pricing",
        ["model_id", sa.text("effective_from DESC")],
    )


def downgrade() -> None:
    op.drop_table("model_pricing")
