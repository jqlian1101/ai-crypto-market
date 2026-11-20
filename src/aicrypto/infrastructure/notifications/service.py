"""推送投递服务（PRD 22.4.4 / 22.4.3）。

约束：

- 投递前检查去重键 ``dedup:{fingerprint}:{revision}``，已成功投递则跳过（记为 SKIPPED）；
- 去重键只在**成功**后写入，失败绝不写入，否则会永久吞掉后续重试；
- 仅对可重试错误按 ``NOTIFY_RETRY_BACKOFF`` 退避，最多 ``NOTIFY_MAX_RETRIES`` 次；
- 重试耗尽或不可重试错误写入 ``notification_dead_letters``；
- 测试消息（``is_test=True``）绕过去重，但仍写入 ``notification_events``。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.orm import Session

from aicrypto.common.clock import now
from aicrypto.config.settings import Settings
from aicrypto.domain.anomaly.dedup import dedup_key
from aicrypto.infrastructure.database.models.anomaly import AnomalyEvent
from aicrypto.infrastructure.database.models.notification import (
    NotificationDeadLetter,
    NotificationEvent,
)
from aicrypto.infrastructure.notifications import feishu
from aicrypto.infrastructure.notifications.render import render_anomaly_card, render_test_card


class DeliveryOutcome:
    """单次投递的最终结果。"""

    def __init__(self, status: str, notification_event_id: str, error: str | None = None) -> None:
        self.status = status  # SUCCESS | FAILED | SKIPPED
        self.notification_event_id = notification_event_id
        self.error = error


def _new_id() -> str:
    return f"ntf_{uuid.uuid4().hex}"


def _parse_backoff(settings: Settings) -> list[int]:
    return settings.retry_backoff_seconds


def _digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def deliver_anomaly(
    db: Session,
    redis: Redis,
    event: AnomalyEvent,
    *,
    settings: Settings,
    sleep: Any = asyncio.sleep,
) -> DeliveryOutcome:
    """投递一条异动事件到飞书，写入 ``notification_events``。"""
    key = dedup_key(event.fingerprint, event.revision)
    if await redis.exists(key):
        return _record_skipped(db, event, reason="dedup_hit")

    payload = render_anomaly_card(event)
    return await _send_with_retry(
        db,
        redis,
        payload=payload,
        settings=settings,
        event=event,
        is_test=False,
        sleep=sleep,
    )


async def deliver_test(
    db: Session,
    redis: Redis,
    *,
    settings: Settings,
    sleep: Any = asyncio.sleep,
) -> DeliveryOutcome:
    """测试消息：绕过去重与配额，但写入记录且 ``is_test=True``（PRD 15.7 / 22.6）。"""
    return await _send_with_retry(
        db,
        redis,
        payload=render_test_card(),
        settings=settings,
        event=None,
        is_test=True,
        sleep=sleep,
    )


async def _send_with_retry(
    db: Session,
    redis: Redis,
    *,
    payload: dict[str, Any],
    settings: Settings,
    event: AnomalyEvent | None,
    is_test: bool,
    sleep: Any,
) -> DeliveryOutcome:
    backoffs = _parse_backoff(settings)
    max_retries = settings.NOTIFY_MAX_RETRIES
    notification_id = _new_id()
    created_at = now()
    retry_count = 0
    last_error: feishu.FeishuDeliveryError | None = None
    last_latency: int | None = None
    last_payload_bytes = 0
    last_response: dict[str, Any] | None = None
    last_response_code: str | None = None

    while True:
        try:
            result = await feishu.send(payload, settings=settings)
        except feishu.FeishuDeliveryError as exc:
            last_error = exc
            if not exc.retryable or retry_count >= max_retries:
                break
            await sleep(backoffs[min(retry_count, len(backoffs) - 1)])
            retry_count += 1
            continue

        last_latency = result.latency_ms
        last_payload_bytes = result.payload_bytes
        last_response = result.response_body
        last_response_code = result.response_code
        _write_event(
            db,
            notification_id=notification_id,
            created_at=created_at,
            event=event,
            payload=payload,
            status="SUCCESS",
            retry_count=retry_count,
            latency_ms=last_latency,
            payload_bytes=last_payload_bytes,
            response_code=last_response_code,
            response_body=last_response,
            is_test=is_test,
            error=None,
        )
        # Redis 去重键与冷却键由调用方（deliver_pending_anomalies）在成功后统一写入，
        # 此处不写，避免两处维护 TTL 与键空间。
        return DeliveryOutcome("SUCCESS", notification_id)

    # 耗尽或不可重试：记录失败并写死信。
    error_message = str(last_error) if last_error else "未知错误"
    error_payload = {
        "message": error_message,
        "code": last_error.code if last_error else None,
    }
    _write_event(
        db,
        notification_id=notification_id,
        created_at=created_at,
        event=event,
        payload=payload,
        status="FAILED",
        retry_count=retry_count,
        latency_ms=last_latency,
        payload_bytes=last_payload_bytes,
        response_code=last_error.code if last_error else None,
        response_body=last_response,
        is_test=is_test,
        error=error_payload,
    )
    db.add(
        NotificationDeadLetter(
            dead_letter_id=f"dlq_{uuid.uuid4().hex}",
            notification_event_id=notification_id,
            channel=feishu.CHANNEL,
            reason=error_message,
            payload_ref=_digest(payload),
            retryable=bool(last_error and last_error.retryable),
            created_at=now(),
        )
    )
    return DeliveryOutcome("FAILED", notification_id, error=error_message)


def _write_event(
    db: Session,
    *,
    notification_id: str,
    created_at: dt.datetime,
    event: AnomalyEvent | None,
    payload: dict[str, Any],
    status: str,
    retry_count: int,
    latency_ms: int | None,
    payload_bytes: int,
    response_code: str | None,
    response_body: dict[str, Any] | None,
    is_test: bool,
    error: dict[str, Any] | None,
) -> None:
    db.add(
        NotificationEvent(
            notification_event_id=notification_id,
            created_at=created_at,
            event_type="SYSTEM_ALERT" if event is None else "ANOMALY",
            severity=event.severity if event else "INFO",
            # AnomalyEvent 本身不含 push_mode（它在 AnomalyRule 上），此处统一按 PUSH 记录，
            # 真实的推送模式在后续接入规则聚合时再从 AnomalyRule.push_mode 读取。
            push_mode="PUSH",
            fingerprint=event.fingerprint if event else None,
            revision=event.revision if event else 0,
            revision_reason=event.revision_reason if event else None,
            channel=feishu.CHANNEL,
            rendered_type="interactive",
            recipient_summary=None,
            payload_digest=_digest(payload),
            payload_bytes=payload_bytes,
            delivery_status=status,
            response_code=response_code,
            response_body=response_body,
            invalid_recipients=None,
            retry_count=retry_count,
            latency_ms=latency_ms,
            is_test=is_test,
            is_backfill=False,
            error=error,
            anomaly_event_id=event.anomaly_event_id if event else None,
            signal_id=None,
            job_run_id=None,
        )
    )


def _record_skipped(db: Session, event: AnomalyEvent, *, reason: str) -> DeliveryOutcome:
    notification_id = _new_id()
    _write_event(
        db,
        notification_id=notification_id,
        created_at=now(),
        event=event,
        payload={"skipped": reason},
        status="SKIPPED",
        retry_count=0,
        latency_ms=None,
        payload_bytes=0,
        response_code=None,
        response_body=None,
        is_test=False,
        error={"reason": reason},
    )
    return DeliveryOutcome("SKIPPED", notification_id)


__all__ = ["DeliveryOutcome", "deliver_anomaly", "deliver_test"]
