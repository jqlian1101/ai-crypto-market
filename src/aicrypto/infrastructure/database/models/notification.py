"""通知推送表模型（PRD 第 30.7 节）。"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import ForeignKeyConstraint, Integer, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aicrypto.infrastructure.database.base import (
    Base,
    bool_column,
    ck,
    desc_idx,
    idx,
    json_column,
    pk,
)

NOTIFICATION_EVENT_TYPES = ("ANOMALY", "SCHEDULED_REPORT", "SYSTEM_ALERT")
NOTIFICATION_CHANNELS = ("feishu", "wecom_app", "wecom_bot")
NOTIFICATION_RENDERED_TYPES = ("interactive", "text", "markdown", "template_card")
NOTIFICATION_DELIVERY_STATUSES = (
    "SUCCESS",
    "PARTIAL",
    "FAILED",
    "RATE_LIMITED",
    "SKIPPED",
    "PENDING",
)
NOTIFICATION_DELIVERY_MODES = ("broadcast", "first_success", "severity_routing")
NOTIFICATION_SEVERITIES = ("INFO", "WARN", "CRITICAL")


class NotificationEvent(Base):
    """推送投递记录（PRD 22.4.4 / 30.7）。

    一条异动事件可产生多条投递记录，每条对应一个 (channel, revision)。
    同一 (fingerprint, revision) 在 TTL 内只能成功投递一次（PRD 22.4.4.1）。
    """

    __tablename__ = "notification_events"
    notification_event_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    push_mode: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    revision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    rendered_type: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_digest: Mapped[str] = mapped_column(Text, nullable=False)
    payload_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="PENDING", server_default="PENDING"
    )
    response_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body: Mapped[Any] = json_column(nullable=True)
    invalid_recipients: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_test: Mapped[bool] = bool_column(default=False)
    is_backfill: Mapped[bool] = bool_column(default=False)
    error: Mapped[Any] = json_column(nullable=True)
    anomaly_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        pk("pk_notif_events", "notification_event_id"),
        ck("notif_events_severity", "severity IN ('INFO','WARN','CRITICAL')"),
        ck(
            "notif_events_status",
            "delivery_status IN ('SUCCESS','PARTIAL','FAILED','RATE_LIMITED','SKIPPED','PENDING')",
        ),
        ForeignKeyConstraint(
            ["anomaly_event_id"],
            ["anomaly_events.anomaly_event_id"],
            name="fk_notif_events_anomaly",
            ondelete="SET NULL",
        ),
        desc_idx("idx_notification_events_created", "created_at"),
        desc_idx("idx_notification_events_status", "delivery_status", "created_at"),
        idx("idx_notification_events_fingerprint", "fingerprint", "revision"),
    )


class NotificationDeadLetter(Base):
    """死信队列（PRD 22.4.3 / 30.7）。

    重试耗尽后的消息进入死信队列，可手动重发。
    """

    __tablename__ = "notification_dead_letters"
    dead_letter_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    notification_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload_ref: Mapped[str] = mapped_column(Text, nullable=False)
    retryable: Mapped[bool] = bool_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        pk("pk_dead_letter", "dead_letter_id"),
        ForeignKeyConstraint(
            ["notification_event_id"],
            ["notification_events.notification_event_id"],
            name="fk_dead_letter_event",
            ondelete="CASCADE",
        ),
        desc_idx("idx_notification_dead_letters_created", "created_at"),
        idx("idx_notification_dead_letters_event", "notification_event_id"),
    )


class NotificationPreferences(Base):
    """用户推送偏好（PRD 22.4.4.2 / 30.7）。

    单行表（id='default'），存储全局推送配置。
    """

    __tablename__ = "notification_preferences"
    id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    daily_quota: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60"
    )
    quiet_hours: Mapped[str] = mapped_column(
        Text, nullable=False, default="00:00-07:00", server_default="00:00-07:00"
    )
    critical_bypass_quiet: Mapped[bool] = bool_column(default=False)
    channel_enabled: Mapped[dict[str, bool]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{\"feishu\": true}'::jsonb")
    )
    delivery_mode: Mapped[str] = mapped_column(
        Text, nullable=False, default="broadcast", server_default="broadcast"
    )
    rules: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(nullable=False)

    __table_args__ = (
        pk("pk_notif_prefs", "id"),
        ck("notif_prefs_mode", "delivery_mode IN ('broadcast','first_success','severity_routing')"),
    )


__all__ = [
    "NOTIFICATION_CHANNELS",
    "NOTIFICATION_DELIVERY_MODES",
    "NOTIFICATION_DELIVERY_STATUSES",
    "NOTIFICATION_EVENT_TYPES",
    "NOTIFICATION_RENDERED_TYPES",
    "NOTIFICATION_SEVERITIES",
    "NotificationDeadLetter",
    "NotificationEvent",
    "NotificationPreferences",
]
