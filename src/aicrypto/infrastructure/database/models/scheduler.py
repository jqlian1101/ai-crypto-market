"""调度与任务执行 ORM 模型（PRD 30.7 / 23.1）。"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import Text, UniqueConstraint, ForeignKey, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aicrypto.infrastructure.database.base import (
    Base,
    TimestampType,
    ck,
    desc_idx,
    idx,
)


class ScheduledJob(Base):
    """任务定义（PRD 23.1.2 任务清单）。

    一行一个任务（如 `analysis_1h`、`anomaly_scan`），包含
    cron / interval 表达式、依赖链、超时、幂等约束与推送策略。

    cron_expr 与 interval_seconds 二选一：
      - `cron_expr` 非空 → 按 cron 时间表触发（固定时间点）；
      - `interval_seconds` 非空 → 按间隔触发（距上次完成 N 秒）；
      - 两者都非空时，优先 cron。
    """

    __tablename__ = "scheduled_jobs"

    job_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    cron_expr: Mapped[str | None] = mapped_column(Text, nullable=True)
    interval_seconds: Mapped[int | None] = mapped_column(nullable=True)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="UTC")
    # 依赖任务 ID 数组（PG TEXT[]）
    dependencies: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    timeout_seconds: Mapped[int] = mapped_column(nullable=False, server_default="600")
    max_retries: Mapped[int] = mapped_column(nullable=False, server_default="3")
    # 补跑上限（默认 24h）
    max_backfill_window_seconds: Mapped[int] = mapped_column(
        nullable=False, server_default="86400"
    )
    # 若任务产出通知事件，记录其类型（ANOMALY / SCHEDULED_REPORT / SYSTEM_ALERT）
    push_event_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 推送模式（AGGREGATE / BROADCAST 等）
    push_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="AGGREGATE")
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    # 任务私有配置（如 `analysis_1h` 的 agent 列表、threshold 等）
    config: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[dt.datetime] = mapped_column(TimestampType, nullable=False)

    __table_args__ = (
        ck(
            "ck_scheduled_jobs_type",
            "job_type IN ('CRON','INTERVAL','EVENT','MANUAL')",
        ),
        # cron_expr 与 interval_seconds 至少有一个非空
        ck(
            "ck_scheduled_jobs_trigger",
            "(cron_expr IS NOT NULL) OR (interval_seconds IS NOT NULL)",
        ),
        idx("idx_scheduled_jobs_enabled", "enabled", "job_type"),
    )


class JobRun(Base):
    """单次任务执行记录（PRD 23.1.3 幂等与补跑）。

    每行记录一次调度执行，包括实际运行时间、耗时、错误、是否补跑。

    幂等键 `idempotency_key = {job_id}:{scheduled_at}` 对应的是
    「某个任务在某个计划时间的执行」。同一幂等键的重复触发会因
    唯一约束而回退，确保幂等性（23.1.3）。
    """

    __tablename__ = "job_runs"

    job_run_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        Text, ForeignKey("scheduled_jobs.job_id", name="fk_job_runs_scheduled"), nullable=False
    )
    # 幂等键：物理唯一约束保证一对 (job_id, scheduled_at) 只有一条记录
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    # 计划执行时间（cron 或 interval 推导出）
    scheduled_at: Mapped[dt.datetime] = mapped_column(TimestampType, nullable=False)
    # 实际开始时间（入队后 worker 拿到租约的时间）
    started_at: Mapped[dt.datetime] = mapped_column(TimestampType, nullable=False)
    # 实际完成时间（失败/成功时记录）
    finished_at: Mapped[dt.datetime | None] = mapped_column(TimestampType, nullable=True)
    # 执行状态
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # 是否补跑（BACKFILL=是、MISSED=错过但超出补跑窗口）
    is_backfill: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    # 重试次数（从 1 开始）
    attempt: Mapped[int] = mapped_column(nullable=False, server_default="1")
    # 失败原因/错误信息（仅当 status=FAILED/TIMEOUT 时填充）
    error: Mapped[Any] = mapped_column(JSONB, nullable=True)
    # 执行结果（如摘要、统计）
    result: Mapped[Any] = mapped_column(JSONB, nullable=True)
    # 分析任务的 run_id 或其他上游任务的输出引用
    output_ref: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_job_runs_idempotency"),
        ck(
            "ck_job_runs_status",
            "status IN ('RUNNING','SUCCESS','FAILED','TIMEOUT','SKIPPED_DEPENDENCY','MISSED','CANCELLED')",
        ),
        desc_idx("idx_job_runs_scheduled", "job_id", "scheduled_at"),
        idx("idx_job_runs_status", "status", "started_at"),
    )


class JobLock(Base):
    """分布式任务锁（PRD 23.1.4 分布式锁）。

    多实例部署时，同一 job 在同一计划时间只能有一个实例执行。
    本表存储 Postgres advisory lock 的租约。

    仅当 SCHEDULER_LOCK_MODE=postgres_advisory 时使用；
    本地单机可用 Redis SET NX（见 `cache.py`）。
    """

    __tablename__ = "job_locks"

    lock_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    job_id: Mapped[str] = mapped_column(Text, nullable=False)
    acquired_at: Mapped[dt.datetime] = mapped_column(TimestampType, nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(TimestampType, nullable=False)

    __table_args__ = (
        idx("idx_job_locks_expires", "expires_at"),
        idx("idx_job_locks_job", "job_id"),
    )
