"""有鉴权的系统事件接口；数据库查询位于 services.system。"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from aicrypto.api.auth import require_auth
from aicrypto.api.dependencies import RequestContext, get_request_context
from aicrypto.api.schemas.system import EventPage, QualityEventPayload, SystemEventPayload
from aicrypto.common.clock import now
from aicrypto.common.responses import success_payload
from aicrypto.infrastructure.database.session import get_db
from aicrypto.services import system

router = APIRouter(prefix="/system", dependencies=[Depends(require_auth)])


@router.get(
    "/events",
    summary="系统事件列表",
    response_model=EventPage[SystemEventPayload],
    response_model_exclude_none=True,
)
def list_system_events(
    db: Annotated[Session, Depends(get_db)],
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(description="不透明游标")] = None,
    severity: Annotated[str | None, Query(description="INFO / WARN / CRITICAL")] = None,
) -> dict[str, Any]:
    rows, pagination = system.list_system_events(db, limit=limit, cursor=cursor, severity=severity)
    payload = success_payload(
        rows, request_id=ctx.request_id, trace_id=ctx.trace_id, generated_at=now()
    )
    payload["meta"]["pagination"] = pagination
    return payload


@router.get(
    "/quality-events", summary="数据质量事件列表", response_model=EventPage[QualityEventPayload]
)
def list_quality_events(
    db: Annotated[Session, Depends(get_db)],
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    symbol: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query(description="不透明游标")] = None,
) -> dict[str, Any]:
    events, pagination = system.list_quality_events(db, limit=limit, symbol=symbol, cursor=cursor)
    payload = success_payload(
        events, request_id=ctx.request_id, trace_id=ctx.trace_id, generated_at=now()
    )
    payload["meta"]["pagination"] = pagination
    return payload


__all__ = ["router"]
