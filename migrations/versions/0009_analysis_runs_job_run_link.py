"""V0009: analysis_runs 关联 job_runs（P5-Part2）。

``job_runs`` 是任务级（一个调度窗口一行），``analysis_runs`` 是标的级
（一个窗口 × 每个 symbol/market_type 一行）。二者通过新增的
``analysis_runs.job_run_id`` 外键关联，避免解析幂等键字符串。

``job_run_id`` 可空：手动触发、历史数据不属于任何调度窗口。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_analysis_runs_job_run_link"
down_revision: str | None = "0008_scheduler_tables"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("job_run_id", sa.Text, nullable=True),
    )
    op.create_foreign_key(
        "fk_analysis_runs_job_run",
        "analysis_runs",
        "job_runs",
        ["job_run_id"],
        ["job_run_id"],
    )
    op.create_index(
        "idx_analysis_runs_job_run",
        "analysis_runs",
        ["job_run_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_analysis_runs_job_run", table_name="analysis_runs")
    op.drop_constraint("fk_analysis_runs_job_run", "analysis_runs", type_="foreignkey")
    op.drop_column("analysis_runs", "job_run_id")
