"""系统事件查询用例；不依赖 HTTP 请求或响应模型。"""

from datetime import datetime
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from aicrypto.common.errors import AICryptoError, ErrorCode
from aicrypto.common.pagination import CursorKey, build_pagination_meta
from aicrypto.infrastructure.database.models.market import MarketDataQualityEvent
from aicrypto.infrastructure.database.models.system import SystemEvent


def _cursor_values(cursor: str, time_field: str) -> tuple[datetime, str]:
    try:
        key = CursorKey.decode(cursor)
        if key is None:
            raise ValueError("缺少游标内容")
        timestamp = key.values.get(time_field)
        event_id = key.values.get("id")
        if not isinstance(timestamp, str) or not isinstance(event_id, str) or not event_id:
            raise ValueError("缺少事件排序键")
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            raise ValueError("游标时间必须包含时区")
        return parsed, event_id
    except ValueError as exc:
        raise AICryptoError(
            ErrorCode.COMMON_BAD_REQUEST,
            message="非法 cursor",
            details={"cursor": cursor},
        ) from exc


def _page(rows: list[Any], limit: int, time_field: str) -> tuple[list[Any], dict[str, Any]]:
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more:
        next_cursor = CursorKey(
            {
                time_field: getattr(rows[-1], time_field).isoformat(),
                "id": rows[-1].id,
            }
        ).encode()
    return rows, build_pagination_meta(
        limit=limit,
        offset=0,
        total=None,
        has_more=has_more,
        next_cursor=next_cursor,
    )


def list_system_events(
    db: Session,
    *,
    limit: int,
    cursor: str | None,
    severity: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stmt = select(SystemEvent).order_by(SystemEvent.occurred_at.desc(), SystemEvent.id.desc())
    if severity:
        stmt = stmt.where(SystemEvent.severity == severity.upper())
    if cursor:
        timestamp, event_id = _cursor_values(cursor, "occurred_at")
        stmt = stmt.where(tuple_(SystemEvent.occurred_at, SystemEvent.id) < (timestamp, event_id))
    rows, pagination = _page(
        list(db.execute(stmt.limit(limit + 1)).scalars()), limit, "occurred_at"
    )
    return [
        {
            "id": row.id,
            "occurred_at": row.occurred_at.isoformat(),
            "severity": row.severity,
            "category": row.category,
            "message": row.message,
            "context": row.context,
        }
        for row in rows
    ], pagination


def list_quality_events(
    db: Session,
    *,
    limit: int,
    symbol: str | None,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stmt = select(MarketDataQualityEvent).order_by(
        MarketDataQualityEvent.detected_at.desc(),
        MarketDataQualityEvent.id.desc(),
    )
    if symbol:
        stmt = stmt.where(MarketDataQualityEvent.symbol == symbol.upper())
    if cursor:
        timestamp, event_id = _cursor_values(cursor, "detected_at")
        stmt = stmt.where(
            tuple_(MarketDataQualityEvent.detected_at, MarketDataQualityEvent.id)
            < (timestamp, event_id)
        )
    rows, pagination = _page(
        list(db.execute(stmt.limit(limit + 1)).scalars()), limit, "detected_at"
    )
    return [
        {
            "id": row.id,
            "detected_at": row.detected_at.isoformat(),
            "symbol": row.symbol,
            "market_type": row.market_type,
            "timeframe": row.timeframe,
            "rule_id": row.rule_id,
            "severity": row.severity,
            "data_quality": row.data_quality,
            "detected_by": row.detected_by,
            "context": row.context,
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        }
        for row in rows
    ], pagination
