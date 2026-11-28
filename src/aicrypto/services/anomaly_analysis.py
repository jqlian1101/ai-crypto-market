"""异动 CRITICAL → 完整分析入队（PRD 22.6）。

流程位置：``anomaly_scan.scan_symbol`` 落库新事件之后、同一事务内调用本模块，
为每个新增的 CRITICAL 事件入队一次 ``trigger=ANOMALY`` 的分析运行。

约束：

- 只有 CRITICAL 入队（22.2.6：INFO 不触发，WARN 可选、当前不做）；
- 幂等键取 ``anomaly_event_id``：同一事件无论扫描多少轮，只会入队一次；
- 与事件落库同事务：事件存在则分析必然入队，事务回滚则两者都不存在，不会出现
  「事件有、分析没有」的半成品状态；
- 入队只写 ``analysis_runs``，不在扫描线程里执行模型调用（分析由 worker 异步消费）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from sqlalchemy.orm import Session

from aicrypto.domain.anomaly.identity import build_event_id
from aicrypto.services.analysis import create_run

#: 触发入队的严重级别（PRD 22.2.6）。
ENQUEUE_SEVERITIES: frozenset[str] = frozenset({"CRITICAL"})
ANOMALY_TRIGGER = "ANOMALY"
ANOMALY_MARKET_TYPE = "USDM_FUTURES"


def anomaly_idempotency_key(anomaly_event_id: str) -> str:
    return f"anomaly:{anomaly_event_id}"


def enqueue_critical_analyses(
    db: Session,
    *,
    symbol: str,
    events: Iterable,
    now: dt.datetime,
) -> list[str]:
    """为本轮新增的 CRITICAL 事件入队分析，返回入队（或已存在）的 ``run_id`` 列表。

    ``events`` 为 ``anomaly_scan.NewEvent`` 序列（只读其 ``fingerprint`` 与 ``severity``）。
    """
    run_ids: list[str] = []
    for event in events:
        if event.severity not in ENQUEUE_SEVERITIES:
            continue
        anomaly_event_id = build_event_id(event.fingerprint, 0)
        payload = create_run(
            db,
            symbol=symbol,
            market_type=ANOMALY_MARKET_TYPE,
            trigger=ANOMALY_TRIGGER,
            now=now,
            idempotency_key=anomaly_idempotency_key(anomaly_event_id),
            analysis_mode="LIVE",
            anomaly_event_id=anomaly_event_id,
        )
        run_ids.append(payload["run_id"])
    return run_ids


__all__ = [
    "ANOMALY_TRIGGER",
    "ENQUEUE_SEVERITIES",
    "anomaly_idempotency_key",
    "enqueue_critical_analyses",
]
