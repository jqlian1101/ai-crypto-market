"""异动事件自动投递（PRD 22.4.4 / 22.4.4.1）。

流程：读取 ``notification_status = 'PENDING'`` 的异动事件 → 逐条投递 → 按结果更新状态。

约束：

- 投递成功后才写去重键与冷却键（``mark_delivered``）；失败不写，保证可重试；
- 状态映射：成功 → ``SENT``；去重命中 → ``SUPPRESSED``；耗尽或不可重试 → ``FAILED``；
- 单条失败不影响同批其余事件；每条独立提交，避免一条异常回滚整批。
"""

from __future__ import annotations

import logging

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from aicrypto.config.settings import Settings
from aicrypto.infrastructure.anomaly_state import mark_delivered
from aicrypto.infrastructure.database.models.anomaly import AnomalyEvent
from aicrypto.infrastructure.notifications.service import deliver_anomaly

logger = logging.getLogger(__name__)

PENDING_BATCH_LIMIT = 50


async def deliver_pending_anomalies(
    db: Session,
    redis: Redis,
    *,
    settings: Settings,
    limit: int = PENDING_BATCH_LIMIT,
) -> dict[str, int]:
    """投递一批 PENDING 异动事件，返回各结果计数。"""
    rows = list(
        db.scalars(
            select(AnomalyEvent)
            .where(AnomalyEvent.notification_status == "PENDING")
            .order_by(AnomalyEvent.detected_at.asc(), AnomalyEvent.anomaly_event_id.asc())
            .limit(limit)
        )
    )

    counts = {"SENT": 0, "SUPPRESSED": 0, "FAILED": 0}
    for event in rows:
        outcome = await deliver_anomaly(db, redis, event, settings=settings)

        if outcome.status == "SUCCESS":
            # 投递成功：写去重键与冷却键，状态置为 SENT。
            # revision 必须与投递前检查时用的完全一致（L-27）：
            # 去重键是 (fingerprint, revision)，写错 revision 等于写了一个
            # 永远不会被检查到的键——升级投递会被反复重投。
            await mark_delivered(
                redis,
                fingerprint=event.fingerprint,
                revision=event.revision,
                cooldown_key=event.cooldown_key,
                severity=event.severity,
                fired_at=event.detected_at,
                critical_seconds=settings.NOTIFY_COOLDOWN_CRITICAL_SECONDS,
                warn_seconds=settings.NOTIFY_COOLDOWN_WARN_SECONDS,
            )
            event.notification_status = "SENT"
            counts["SENT"] += 1
        elif outcome.status == "SKIPPED":
            # 去重命中：该 (fingerprint, revision) 已投递过，不再写 Redis。
            event.notification_status = "SUPPRESSED"
            counts["SUPPRESSED"] += 1
        else:
            event.notification_status = "FAILED"
            counts["FAILED"] += 1
            logger.warning(
                "anomaly_delivery_failed",
                extra={"anomaly_event_id": event.anomaly_event_id, "error": outcome.error},
            )

        db.commit()

    return counts


__all__ = ["PENDING_BATCH_LIMIT", "deliver_pending_anomalies"]
