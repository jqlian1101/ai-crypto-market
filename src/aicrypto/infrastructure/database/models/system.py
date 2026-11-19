"""配置、审计与系统持久化模型（PRD 30.7）。"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from aicrypto.infrastructure.database.base import Base, JsonType, ck, idx, json_column


class ConfigVersion(Base):
    __tablename__ = "config_versions"
    version_id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    applied_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    app_version: Mapped[str] = mapped_column(Text, nullable=False)
    migration_revision: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (idx("idx_config_versions_applied", "applied_at"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[Any] = json_column()
    __table_args__ = (
        idx("idx_audit_logs_occurred", "occurred_at"),
        idx("idx_audit_logs_resource", "resource_type", "resource_id"),
    )


class SystemEvent(Base):
    __tablename__ = "system_events"
    id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[Any] = json_column()
    __table_args__ = (
        idx("idx_system_events_occurred", "occurred_at"),
        ck("system_events_severity", "severity IN ('INFO','WARN','CRITICAL')"),
    )


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"
    id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    invoked_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    arguments: Mapped[Any] = json_column()
    result_summary: Mapped[str | None] = mapped_column(JsonType, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (idx("idx_tool_invocations_agent", "agent_name", "invoked_at"),)
