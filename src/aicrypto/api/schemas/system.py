"""探针及系统事件 HTTP 契约。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from aicrypto.common.pagination import PaginationMeta
from aicrypto.common.responses import ResponseMeta
from aicrypto.domain.market.quality import DataQuality, Severity


class HealthPayload(BaseModel):
    status: str
    timestamp: datetime


class ReadyPayload(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, str]


class SystemEventPayload(BaseModel):
    id: str
    occurred_at: datetime
    severity: Severity
    category: str
    message: str
    context: dict[str, Any]


class QualityEventPayload(BaseModel):
    id: str
    detected_at: datetime
    symbol: str | None
    market_type: str | None
    timeframe: str | None
    rule_id: str
    severity: Severity
    data_quality: DataQuality
    detected_by: str
    context: dict[str, Any]
    resolved_at: datetime | None


class PaginatedMeta(ResponseMeta):
    pagination: PaginationMeta


class EventPage[T](BaseModel):
    data: list[T]
    meta: PaginatedMeta
