"""分析完成后为异动事件生成 AI 解读升级行（revision 1，PRD 22.4.4.1 / 22.6）。

流程位置：``analysis_worker`` 报告落库之后，同一事务内调用本模块。

约束：

- 只对 ``trigger=ANOMALY`` 且带 ``anomaly_event_id`` 的分析运行生效；
- 通过 ``analysis_runs.anomaly_event_id`` 反查首投事件（revision 0），不靠字符串反推；
- 升级行与首投共享 ``fingerprint``，``revision=1``；``ON CONFLICT`` 保证同一分析重跑不重复插入；
- 判定逻辑由 ``decide_ai_revision`` 给出，本模块只负责落库。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aicrypto.domain.anomaly.identity import build_event_id
from aicrypto.domain.anomaly.revision import (
    REASON_AI_INTERPRETATION_READY,
    REVISION_AI_INTERPRETATION,
    decide_ai_revision,
)
from aicrypto.infrastructure.database.models.anomaly import AnomalyEvent
from aicrypto.infrastructure.database.models.report import AnalysisRunRecord


def create_ai_revision(
    db: Session,
    *,
    run_id: str,
    run_status: str,
    degraded_reason: str | None,
) -> str | None:
    """为触发该分析的异动事件写入 revision 1 行，返回新事件 ID；不需要或已存在时返回 None。"""
    run = db.get(AnalysisRunRecord, run_id)
    if run is None or run.trigger != "ANOMALY" or run.anomaly_event_id is None:
        return None

    decision = decide_ai_revision(run_status=run_status, degraded_reason=degraded_reason)
    if not decision.emit:
        return None

    base = db.scalars(
        select(AnomalyEvent).where(AnomalyEvent.anomaly_event_id == run.anomaly_event_id)
    ).one_or_none()
    if base is None:
        return None

    new_id = build_event_id(base.fingerprint, REVISION_AI_INTERPRETATION)
    values: dict[str, Any] = {
        "anomaly_event_id": new_id,
        "detected_at": base.detected_at,
        "symbol": base.symbol,
        "market_type": base.market_type,
        "rule_id": base.rule_id,
        "rule_version": base.rule_version,
        "severity": base.severity,
        "detected_by": base.detected_by,
        "window_start": base.window_start,
        "window_end": base.window_end,
        "market_snapshot_id": run.market_snapshot_id,
        "metrics": base.metrics,
        "threshold": base.threshold,
        "direction": base.direction,
        "cooldown_key": base.cooldown_key,
        "fingerprint": base.fingerprint,
        "revision": REVISION_AI_INTERPRETATION,
        "revision_reason": REASON_AI_INTERPRETATION_READY,
        "llm_interpretation_id": run.run_id,
        "notification_status": "PENDING",
    }
    stmt = (
        insert(AnomalyEvent)
        .values(**values)
        .on_conflict_do_nothing(constraint="uq_anomaly_events_fp_rev")
        .returning(AnomalyEvent.anomaly_event_id)
    )
    inserted = db.execute(stmt).scalar_one_or_none()
    return inserted


__all__ = ["create_ai_revision"]
