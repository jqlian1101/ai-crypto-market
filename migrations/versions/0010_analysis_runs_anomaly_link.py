"""V0010: analysis_runs 关联 anomaly_events（PRD 22.6）。

异动 CRITICAL 触发的分析运行需要能精确指回触发它的事件，
供 AI 解读增强卡片（revision 1）找到对应事件。

``anomaly_event_id`` 可空：定时 / 手动触发的分析不由异动产生。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_analysis_runs_anomaly_link"
down_revision: str | None = "0009_analysis_runs_job_run_link"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("anomaly_event_id", sa.Text, nullable=True),
    )
    op.create_foreign_key(
        "fk_analysis_runs_anomaly_event",
        "analysis_runs",
        "anomaly_events",
        ["anomaly_event_id"],
        ["anomaly_event_id"],
    )
    op.create_index(
        "idx_analysis_runs_anomaly_event",
        "analysis_runs",
        ["anomaly_event_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_analysis_runs_anomaly_event", table_name="analysis_runs")
    op.drop_constraint("fk_analysis_runs_anomaly_event", "analysis_runs", type_="foreignkey")
    op.drop_column("analysis_runs", "anomaly_event_id")
