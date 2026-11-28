"""调度任务入队服务（PRD 23.1 / 20.2）。

调度器计算出到期窗口后，对每个 (symbol, market_type) 调用 :func:`enqueue_analysis`。
入队是幂等的：运行级幂等键 ``{job_id}:{scheduled_at}|{symbol}|{market_type}``
由 ``analysis_runs.idempotency_key`` 唯一约束兜底。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from aicrypto.services.analysis import create_run
from aicrypto.services.scheduler_core import make_run_idempotency_key

# 日报类任务：trigger 用 RETROSPECTIVE，使 tier_for_trigger 解析为 DEEP（PRD 24.3）。
# 其余定时任务保持 SCHEDULED（STANDARD）。
_DEEP_TRIGGER_JOBS = frozenset({"analysis_daily"})


def trigger_for_job(job_id: str) -> str:
    """按调度任务决定 analysis_runs.trigger。"""
    return "RETROSPECTIVE" if job_id in _DEEP_TRIGGER_JOBS else "SCHEDULED"


def enqueue_analysis(
    db: Session,
    *,
    symbol: str,
    market_type: str,
    job_id: str,
    scheduled_at: dt.datetime,
    job_run_id: str,
    now: dt.datetime,
    is_backfill: bool = False,
) -> dict[str, Any]:
    """为一个窗口入队一次分析运行（幂等）。

    - ``trigger``：日报类任务为 ``RETROSPECTIVE``（走 DEEP 档），其余为 ``SCHEDULED``；
    - ``analysis_mode``：补跑为 ``BACKFILL``，否则 ``LIVE``；
    - ``job_run_id`` 是所属的调度窗口记录 ID；
    - 同一幂等键重复入队，``create_run`` 返回已存在的记录。
    """
    return create_run(
        db,
        symbol=symbol,
        market_type=market_type,
        trigger=trigger_for_job(job_id),
        now=now,
        idempotency_key=make_run_idempotency_key(
            job_id, scheduled_at, symbol=symbol, market_type=market_type
        ),
        analysis_mode="BACKFILL" if is_backfill else "LIVE",
        job_run_id=job_run_id,
    )
