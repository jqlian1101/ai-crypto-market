"""V0008: 调度与任务执行表（scheduled_jobs、job_runs、job_locks）。

PRD 第 23 章（调度与定时分析）与 30.7 节（字段定义）。

**设计要点**：

1. `scheduled_jobs`：任务定义表，每行一个任务（如 `analysis_1h`），
   包含 cron / interval 表达式、依赖链、超时与推送策略。

2. `job_runs`：执行记录表，每行一次调度执行。幂等键为
   `{job_id}:{scheduled_at}`，由唯一约束保证同一窗口仅一条记录。

3. `job_locks`：分布式锁表，仅在 Postgres advisory lock 模式下使用；
   本地单机用 Redis SET NX 即可。

**幂等性设计**（23.1.3）：

- 同一幂等键的重复触发会因唯一约束而冲突；
- 第一次插入成功则 `status=RUNNING`；
- 重试方通过重新查询已有行来获取结果，避免重复执行。
- 补跑（BACKFILL）与错过（MISSED）通过 `is_backfill` 与 `status` 区分。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008_scheduler_tables"
down_revision: str | None = "0007_prompt_versions_tmpl_name"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ========================================================================
    # scheduled_jobs 表
    # ========================================================================
    op.create_table(
        "scheduled_jobs",
        sa.Column("job_id", sa.Text, nullable=False, primary_key=True),
        sa.Column("job_type", sa.Text, nullable=False),
        sa.Column("cron_expr", sa.Text, nullable=True),
        sa.Column("interval_seconds", sa.Integer, nullable=True),
        sa.Column("timezone", sa.Text, nullable=False, server_default="UTC"),
        sa.Column("dependencies", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("timeout_seconds", sa.Integer, nullable=False, server_default="600"),
        sa.Column("max_retries", sa.Integer, nullable=False, server_default="3"),
        sa.Column(
            "max_backfill_window_seconds",
            sa.Integer,
            nullable=False,
            server_default="86400",
        ),
        sa.Column("push_event_type", sa.Text, nullable=True),
        sa.Column("push_mode", sa.Text, nullable=False, server_default="AGGREGATE"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "job_type IN ('CRON','INTERVAL','EVENT','MANUAL')",
            name="ck_scheduled_jobs_type",
        ),
        sa.CheckConstraint(
            "(cron_expr IS NOT NULL) OR (interval_seconds IS NOT NULL)",
            name="ck_scheduled_jobs_trigger",
        ),
    )
    op.create_index(
        "idx_scheduled_jobs_enabled",
        "scheduled_jobs",
        ["enabled", "job_type"],
    )

    # ========================================================================
    # job_runs 表
    # ========================================================================
    op.create_table(
        "job_runs",
        sa.Column("job_run_id", sa.Text, nullable=False, primary_key=True),
        sa.Column(
            "job_id",
            sa.Text,
            sa.ForeignKey("scheduled_jobs.job_id", name="fk_job_runs_scheduled"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("is_backfill", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
        sa.Column("error", postgresql.JSONB, nullable=True),
        sa.Column("result", postgresql.JSONB, nullable=True),
        sa.Column("output_ref", sa.Text, nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_job_runs_idempotency"),
        sa.CheckConstraint(
            "status IN ('RUNNING','SUCCESS','FAILED','TIMEOUT','SKIPPED_DEPENDENCY','MISSED','CANCELLED')",
            name="ck_job_runs_status",
        ),
    )
    op.create_index(
        "idx_job_runs_scheduled",
        "job_runs",
        ["job_id", sa.text("scheduled_at DESC")],
    )
    op.create_index(
        "idx_job_runs_status",
        "job_runs",
        ["status", sa.text("started_at DESC")],
    )

    # ========================================================================
    # job_locks 表（分布式锁）
    # ========================================================================
    op.create_table(
        "job_locks",
        sa.Column("lock_id", sa.Text, nullable=False, primary_key=True),
        sa.Column("job_id", sa.Text, nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_job_locks_expires",
        "job_locks",
        ["expires_at"],
    )
    op.create_index(
        "idx_job_locks_job",
        "job_locks",
        ["job_id"],
    )


def downgrade() -> None:
    op.drop_table("job_locks")
    op.drop_table("job_runs")
    op.drop_table("scheduled_jobs")
