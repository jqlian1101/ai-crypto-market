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


def reconcile_job_run(db: Session, job_run: JobRun, *, now: dt.datetime) -> None:
    """对一个 job_run 进行对账：根据下属 analysis_runs 的终态聚合，更新 job_run。

    **流程**：
    1. 查询所有 job_run_id 匹配的 analysis_runs
    2. 提取它们的 status，调用 aggregate_job_status
    3. 若聚合结果为终态（非 None），更新 job_run

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
