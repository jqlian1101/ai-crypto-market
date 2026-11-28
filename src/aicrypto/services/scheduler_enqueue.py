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

    - ``trigger`` 固定为 ``SCHEDULED``；
    - ``analysis_mode``：补跑为 ``BACKFILL``，否则 ``LIVE``；
    - ``job_run_id`` 是所属的调度窗口记录 ID；
    - 同一幂等键重复入队，``create_run`` 返回已存在的记录。
    """
    return create_run(
        db,
        symbol=symbol,
        market_type=market_type,
        trigger="SCHEDULED",
        now=now,
        idempotency_key=make_run_idempotency_key(
            job_id, scheduled_at, symbol=symbol, market_type=market_type
        ),
        analysis_mode="BACKFILL" if is_backfill else "LIVE",
        job_run_id=job_run_id,
    )
