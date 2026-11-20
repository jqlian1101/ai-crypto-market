"""异动规则与事件持久化模型（PRD 第 30.6 章）。"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import ForeignKeyConstraint, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from aicrypto.infrastructure.database.base import (
    Base,
    bool_column,
    ck,
    desc_idx,
    idx,
    json_column,
    pk,
    uq,
)

SEVERITIES = ("INFO", "WARN", "CRITICAL")
PUSH_MODES = ("PUSH", "AGGREGATE", "DIGEST")
RULE_CATEGORIES = ("PRICE", "VOLATILITY", "VOLUME", "DERIVATIVES", "DATA_SYSTEM")
NOTIFICATION_STATUSES = ("PENDING", "SENT", "SUPPRESSED", "FAILED")


class AnomalyRule(Base):
    """异动规则定义与版本。修改阈值插入新版本，不修改旧行（30.6）。"""

    __tablename__ = "anomaly_rules"
    rule_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    rule_version: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    push_mode: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[Any] = json_column()
    asset_overrides: Mapped[Any] = json_column()
    market_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="USDM_FUTURES", server_default="USDM_FUTURES"
    )
    enabled: Mapped[bool] = bool_column(default=True)
    effective_from: Mapped[dt.datetime] = mapped_column(nullable=False)
    effective_to: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    change_note: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        pk("pk_anomaly_rules", "rule_id", "rule_version"),
        ck("anomaly_rules_severity", "severity IN ('INFO','WARN','CRITICAL')"),
        ck("anomaly_rules_push_mode", "push_mode IN ('PUSH','AGGREGATE','DIGEST')"),
        ck(
            "anomaly_rules_category",
            "category IN ('PRICE','VOLATILITY','VOLUME','DERIVATIVES','DATA_SYSTEM')",
        ),
    )


class AnomalyEvent(Base):
    """异动事件（PRD 22.3 / 30.6）。

    ``fingerprint`` 标识事件身份，不随分析结果变化；``(fingerprint, revision)``
    为唯一键，用于投递轮次区分（22.4.4.1）。
    """

    __tablename__ = "anomaly_events"
    anomaly_event_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    detected_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    market_type: Mapped[str] = mapped_column(Text, nullable=False)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    detected_by: Mapped[str] = mapped_column(
        Text, nullable=False, default="RULE_ENGINE", server_default="RULE_ENGINE"
    )
    window_start: Mapped[dt.datetime] = mapped_column(nullable=False)
    window_end: Mapped[dt.datetime] = mapped_column(nullable=False)
    market_snapshot_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[Any] = json_column()
    threshold: Mapped[Any] = json_column()
    direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    cooldown_key: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    revision_reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="INITIAL_DETERMINISTIC",
        server_default="INITIAL_DETERMINISTIC",
    )
    llm_interpretation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    notification_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="PENDING", server_default="PENDING"
    )

    __table_args__ = (
        pk("pk_anomaly_events", "anomaly_event_id"),
        ForeignKeyConstraint(
            ["rule_id", "rule_version"],
            ["anomaly_rules.rule_id", "anomaly_rules.rule_version"],
            name="fk_anomaly_events_rule",
        ),
        uq("fingerprint", "revision", name="uq_anomaly_events_fp_rev"),
        desc_idx("idx_anomaly_events_detected", "detected_at"),
        idx("idx_anomaly_events_rule", "rule_id", "detected_at"),
        ck("anomaly_events_severity", "severity IN ('INFO','WARN','CRITICAL')"),
        ck("anomaly_events_direction", "direction IS NULL OR direction IN ('UP','DOWN','NEUTRAL')"),
        ck(
            "anomaly_events_notification_status",
            "notification_status IN ('PENDING','SENT','SUPPRESSED','FAILED')",
        ),
        ck("anomaly_events_revision", "revision >= 0"),
    )


__all__ = [
    "NOTIFICATION_STATUSES",
    "PUSH_MODES",
    "RULE_CATEGORIES",
    "SEVERITIES",
    "AnomalyEvent",
    "AnomalyRule",
]
