"""调度层对账与回写（ORM 层）。

在每次调度 tick 中调用：扫描 RUNNING 的 job_runs，根据下属 analysis_runs 的终态聚合，
更新 job_run 的完成状态。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aicrypto.infrastructure.database.models.report import AnalysisRunRecord
from aicrypto.infrastructure.database.models.scheduler import JobRun, ScheduledJob
from aicrypto.services.scheduler_reconcile import aggregate_job_status, is_timed_out

import structlog

logger = structlog.get_logger(__name__)


def reconcile_job_run(db: Session, job_run: JobRun, *, now: dt.datetime) -> None:
    """对一个 job_run 进行对账：根据下属 analysis_runs 的终态聚合，更新 job_run。

    **流程**：
    1. 查询所有 job_run_id 匹配的 analysis_runs
    2. 提取它们的 status，调用 aggregate_job_status
    3. 若聚合结果为终态（非 None），更新 job_run
    4. 若为 FAILED 且可重试，创建重试窗口

    **结果格式**（写入 job_run.result）：
    ```json
    {
        "analysis_runs": [
            {"run_id": "...", "symbol": "BTC", "market_type": "spot", "status": "COMPLETE"},
            ...
        ]
    }
    ```
    """
    # 查询所有属于该 job_run 的 analysis_runs
    analysis_runs = db.scalars(
        select(AnalysisRunRecord).where(AnalysisRunRecord.job_run_id == job_run.job_run_id)
    ).all()

    if not analysis_runs:
        # 若没有任何下属 analysis_run，视为未开始（不应回写）
        return

    statuses = [run.status for run in analysis_runs]
    aggregated_status = aggregate_job_status(statuses)

    if aggregated_status is None:
        # 仍未全部终态，不更新 job_run
        return

    # 聚合成功，更新 job_run
    job_run.status = aggregated_status
    job_run.finished_at = now
    job_run.result = {
        "analysis_runs": [
            {
                "run_id": run.run_id,
                "symbol": run.symbol,
                "market_type": run.market_type,
                "status": run.status,
            }
            for run in analysis_runs
        ]
    }

    # 若为 FAILED，检查是否可重试
    if aggregated_status == "FAILED":
        from aicrypto.config.settings import get_settings
        from aicrypto.services.scheduler_enqueue import enqueue_analysis
        from aicrypto.services.scheduler_reconcile import should_retry, next_retry_at
        from aicrypto.common.ids import new_job_run_id

        settings = get_settings()
        attempt = job_run.attempt or 1
        max_retries = settings.SCHEDULER_MAX_RETRIES

        if should_retry(aggregated_status, attempt, max_retries):
            # 计算下次重试时间
            retry_at = next_retry_at(
                failed_at=job_run.started_at,
                attempt=attempt,
                base=dt.timedelta(seconds=settings.SCHEDULER_RETRY_BASE_DELAY_SECONDS),
            )

            # 创建重试窗口
            new_job_run = JobRun(
                job_run_id=new_job_run_id(),
                job_id=job_run.job_id,
                scheduled_at=retry_at,
                attempt=attempt + 1,
                status="QUEUED",
                is_backfill=False,
            )
            db.add(new_job_run)
            db.flush()

            # 为重试窗口重新入队分析任务
            # 注：这里需要获取标的和市场类型清单
            # 从当前 job_run 的 analysis_runs 中提取
            symbols_to_retry = {run.symbol for run in analysis_runs}
            market_types_to_retry = {run.market_type for run in analysis_runs}

            for symbol in symbols_to_retry:
                for market_type in market_types_to_retry:
                    enqueue_analysis(
                        db,
                        symbol=symbol,
                        market_type=market_type,
                        job_id=job_run.job_id,
                        scheduled_at=retry_at,
                        job_run_id=new_job_run.job_run_id,
                        now=now,
                        is_backfill=False,
                    )

            import structlog
            logger = structlog.get_logger(__name__)
            logger.info(
                "scheduled_retry",
                job_id=job_run.job_id,
                attempt=attempt + 1,
                retry_at=retry_at,
                new_job_run_id=new_job_run.job_run_id,
            )


def reconcile_running_jobs(db: Session, *, now: dt.datetime) -> dict[str, Any]:
    """对账所有 RUNNING 的 job_runs。

    返回统计信息：已完成数、仍运行数、超时数等。
    """
    running_jobs = db.scalars(
        select(JobRun).where(JobRun.status == "RUNNING").order_by(JobRun.started_at)
    ).all()

    reconciled_count = 0
    timeout_count = 0

    for job_run in running_jobs:
        # 超时责任归属 analysis_run 层（claim_run 已对 RUNNING 超预算的置 TIMEOUT）。
        # job_run 不得按自身 started_at 计时：入队后可能长时间排队，
        # 那段时间不代表执行超时，否则会误标 TIMEOUT 且无法再回写。
        # 仅当窗口完全没有下属 analysis_run（入队中途失败的残留）且超过
        # 任务超时，才判为 TIMEOUT。
        has_children = (
            db.scalar(
                select(AnalysisRunRecord.run_id)
                .where(AnalysisRunRecord.job_run_id == job_run.job_run_id)
                .limit(1)
            )
            is not None
        )
        if not has_children:
            job_def = db.scalar(select(ScheduledJob).where(ScheduledJob.job_id == job_run.job_id))
            timeout_seconds = job_def.timeout_seconds if job_def is not None else 600
            if is_timed_out(job_run.started_at, timeout_seconds, now=now):
                job_run.status = "TIMEOUT"
                job_run.finished_at = now
                job_run.error = {"code": "SCHEDULER_NO_ANALYSIS_RUNS"}
                timeout_count += 1
            continue

        # 有下属分析：只做聚合，终态由 analysis_run 的实际结果决定
        reconcile_job_run(db, job_run, now=now)
        if job_run.finished_at is not None:
            reconciled_count += 1

    return {
        "reconciled_count": reconciled_count,
        "timeout_count": timeout_count,
        "still_running": len(running_jobs) - reconciled_count - timeout_count,
    }


def record_missed_windows(
    db: Session,
    job_id: str,
    missed_windows: list[dt.datetime],
    now: dt.datetime,
) -> int:
    """将 MISSED 窗口记录到 job_runs 表。
    
    **语义**：
    - 超出补跑上限的计划时间被标记为 MISSED
    - 这些窗口不入队任何分析任务，仅作为历史记录
    - 便于日报统计「有多少个分析窗口被错过」
    
    **幂等性**：
    - 通过 idempotency_key 检查避免重复插入
    - 同一窗口多次调用不会产生重复记录
    
    :param db: 数据库会话
    :param job_id: 任务 ID
    :param missed_windows: 超出补跑上限的计划时间列表
    :param now: 当前时间
    :return: 插入的行数
    """
    from aicrypto.common.ids import new_job_run_id
    from aicrypto.services.scheduler_core import make_idempotency_key

    inserted = 0
    for scheduled_at in missed_windows:
        # 检查幂等键是否已存在（避免重复插入）
        idem_key = make_idempotency_key(job_id, scheduled_at)
        existing = db.scalar(
            select(JobRun).where(JobRun.idempotency_key == idem_key).limit(1)
        )

        if existing:
            logger.debug("missed_window_already_recorded", idempotency_key=idem_key)
            continue

        job_run = JobRun(
            job_run_id=new_job_run_id(),
            job_id=job_id,
            idempotency_key=idem_key,
            scheduled_at=scheduled_at,
            started_at=now,
            status="MISSED",
            is_backfill=False,
        )
        db.add(job_run)
        inserted += 1

    if inserted > 0:
        db.flush()
        logger.info(
            "missed_windows_recorded",
            job_id=job_id,
            count=inserted,
            windows_count=len(missed_windows),
        )

    return inserted
