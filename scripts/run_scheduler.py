"""调度器单次 tick 入口：计算并入队应该执行的任务。

用法：从项目根目录运行

    .venv\\Scripts\\python.exe scripts\\run_scheduler.py

供外部调度（Windows 任务计划程序 / cron）周期调用，每次执行一个时间点。
不常驻运行，单次完成后退出。

**初始任务清单**（PRD 23.1.2）：

| 任务 ID | 类型 | 触发时间（UTC） | 描述 |
|---|---|---|---|
| `analysis_1h` | CRON | `5 * * * *` | 每小时第 5 分钟，综合分析 |
| `analysis_15m` | CRON | `*/15 * * * *` | 每 15 分钟，结构分析 |
| `analysis_4h` | CRON | `7 */4 * * *` | 每 4 小时第 7 分钟，慢周期 |
| `analysis_daily` | CRON | `0 8 * * *` | 每日 08:00 UTC，日报 |
| `anomaly_scan` | INTERVAL | 60s | 每分钟异动扫描（见 scripts/run_anomaly_scan.py） |

**执行流程**：

1. 读取所有启用的任务定义（从 `scheduled_jobs` 表，或初期硬编码）；
2. 对每个任务计算从上次完成到现在的缺失窗口；
3. 对每个窗口尝试入队：幂等键冲突则跳过（已入队过），否则新建任务记录；
4. 记录 MISSED 窗口（超出补跑上限的）供日报展示。
"""

from __future__ import annotations

import sys
from datetime import datetime

from aicrypto.common.clock import now
from aicrypto.config.market_symbols import resolve_market_symbols
from aicrypto.config.settings import get_settings
from aicrypto.infrastructure.database.session import session_scope
from aicrypto.services.scheduler_core import JobSchedule, ScheduledWindow, compute_backfill_windows
from aicrypto.services.scheduler_enqueue import enqueue_analysis

# 初始任务清单（硬编码，后期改为从数据库读取）
INITIAL_JOBS = [
    JobSchedule(
        job_id="analysis_1h",
        job_type="CRON",
        cron_expr="5 * * * *",  # 每小时第 5 分钟
        interval_seconds=None,
        timezone="UTC",
        max_backfill_window_seconds=86400,  # 24h 补跑上限
        enabled=True,
    ),
    JobSchedule(
        job_id="analysis_15m",
        job_type="CRON",
        cron_expr="*/15 * * * *",  # 每 15 分钟
        interval_seconds=None,
        timezone="UTC",
        max_backfill_window_seconds=86400,
        enabled=True,
    ),
    JobSchedule(
        job_id="analysis_4h",
        job_type="CRON",
        cron_expr="7 */4 * * *",  # 每 4 小时第 7 分钟
        interval_seconds=None,
        timezone="UTC",
        max_backfill_window_seconds=86400,
        enabled=True,
    ),
    JobSchedule(
        job_id="analysis_daily",
        job_type="CRON",
        cron_expr="0 8 * * *",  # 每日 08:00 UTC
        interval_seconds=None,
        timezone="UTC",
        max_backfill_window_seconds=86400,
        enabled=True,
    ),
]


def _record_dispatch(db, window: ScheduledWindow, moment: datetime) -> str:
    """记录一个任务窗口的调度：在 job_runs 表插入一行。

    这一行标记「该任务在该计划时间被调度了」，用于后续查询上次成功时间。

    返回新插入的 job_run_id（或已存在的，若幂等键冲突）。
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from aicrypto.common.ids import new_job_run_id
    from aicrypto.infrastructure.database.models.scheduler import JobRun
    from aicrypto.services.scheduler_core import make_idempotency_key

    idem_key = make_idempotency_key(window.job_id, window.scheduled_at)
    # 先查：已存在则直接复用其 job_run_id，避免同一窗口多次 tick 产生不同 ID
    existing = db.scalar(select(JobRun.job_run_id).where(JobRun.idempotency_key == idem_key))
    if existing is not None:
        return existing

    job_run_id = new_job_run_id()
    stmt = (
        insert(JobRun)
        .values(
            job_run_id=job_run_id,
            job_id=window.job_id,
            idempotency_key=idem_key,
            scheduled_at=window.scheduled_at,
            started_at=moment,
            status="RUNNING",
            is_backfill=window.is_backfill,
        )
        .on_conflict_do_nothing(index_elements=[JobRun.idempotency_key])
    )
    result = db.execute(stmt)
    # 若冲突（幂等键已存在），查询已有的 job_run_id
    if result.rowcount == 0:
        existing = db.scalar(select(JobRun.job_run_id).where(JobRun.idempotency_key == idem_key))
        return existing
    return job_run_id


def _last_success_scheduled_at(db, job_id: str) -> datetime | None:
    """读取该任务最近一次成功执行的计划时间（job_runs.scheduled_at）。"""
    from sqlalchemy import func, select

    from aicrypto.infrastructure.database.models.scheduler import JobRun

    stmt = (
        select(func.max(JobRun.scheduled_at))
        .where(JobRun.job_id == job_id)
        .where(JobRun.status == "SUCCESS")
    )
    return db.execute(stmt).scalar_one_or_none()


def main() -> int:
    """单次调度 tick，计算并入队应该执行的任务。"""
    settings = get_settings()

    if not settings.SCHEDULER_ENABLED:
        print("scheduler_disabled: SCHEDULER_ENABLED=false", flush=True)
        return 0

    moment = now()
    symbols = resolve_market_symbols(settings)
    enqueued_count = 0
    missed_count = 0

    with session_scope(settings) as db:
        # 同步 INITIAL_JOBS 到 scheduled_jobs 表，确保 job_runs.job_id 外键不会失败
        from sqlalchemy.dialects.postgresql import insert

        from aicrypto.infrastructure.database.models.scheduler import ScheduledJob

        for job_def in INITIAL_JOBS:
            stmt = (
                insert(ScheduledJob)
                .values(
                    job_id=job_def.job_id,
                    job_type=job_def.job_type,
                    cron_expr=job_def.cron_expr,
                    interval_seconds=job_def.interval_seconds,
                    timezone=job_def.timezone,
                    timeout_seconds=600,  # 默认 10 分钟
                    max_backfill_window_seconds=job_def.max_backfill_window_seconds,
                    enabled=job_def.enabled,
                    updated_at=moment,
                )
                .on_conflict_do_nothing(index_elements=[ScheduledJob.job_id])
            )
            db.execute(stmt)
        db.commit()

        for job in INITIAL_JOBS:
            if not job.enabled:
                continue

            # 上次成功执行的计划时间：从 job_runs 读取，避免每次 tick 都回溯窗口
            last_success_at = _last_success_scheduled_at(db, job.job_id)

            windows, missed_windows = compute_backfill_windows(
                job,
                now=moment,
                last_success_at=last_success_at,
            )

            # 入队待执行窗口
            for window in windows:
                # 每个窗口一个 SAVEPOINT：单窗口失败只回滚它自己，不影响其他窗口
                try:
                    with db.begin_nested():
                        job_run_id = _record_dispatch(db, window, moment)
                        for symbol in symbols:
                            for market_type in settings.market_types:
                                enqueue_analysis(
                                    db,
                                    symbol=symbol,
                                    market_type=market_type.value,
                                    job_id=window.job_id,
                                    scheduled_at=window.scheduled_at,
                                    job_run_id=job_run_id,
                                    now=moment,
                                    is_backfill=window.is_backfill,
                                )
                                enqueued_count += 1
                except Exception as exc:
                    print(
                        f"enqueue_failed: job_id={window.job_id} "
                        f"scheduled_at={window.scheduled_at} error={exc}",
                        file=sys.stderr,
                        flush=True,
                    )

            missed_count += len(missed_windows)
            if missed_windows:
                print(
                    f"scheduler_missed: job_id={job.job_id} windows={missed_windows}",
                    flush=True,
                )

        # 对账回写：扫描 RUNNING 的 job_runs，更新为终态或检测超时
        from aicrypto.services.scheduler_reconcile_db import reconcile_running_jobs

        recon_stats = reconcile_running_jobs(db, now=moment)
        db.commit()

        print(
            f"scheduler_tick_complete: enqueued={enqueued_count} "
            f"missed={missed_count} reconciled={recon_stats['reconciled_count']} "
            f"timeout={recon_stats['timeout_count']} still_running={recon_stats['still_running']} "
            f"duration_ms={round((now() - moment).total_seconds() * 1000)}",
            flush=True,
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
