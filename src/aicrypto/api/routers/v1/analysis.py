"""AI 分析报告 API。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from aicrypto.api.auth import require_auth
from aicrypto.api.dependencies import RequestContext, get_request_context
from aicrypto.api.schemas.analysis import (
    AnalysisFeedbackRequest,
    AnalysisFeedbackResponse,
    AnalysisReportSchema,
    AnalysisRunRequest,
    AnalysisRunSchema,
)
from aicrypto.common.clock import now
from aicrypto.common.responses import SuccessResponse, success_payload
from aicrypto.infrastructure.database.session import get_db
from aicrypto.services import analysis

router = APIRouter(prefix="/analysis", dependencies=[Depends(require_auth)])


@router.post("/runs", response_model=SuccessResponse[AnalysisRunSchema], status_code=202)
def create_analysis_run(
    request: AnalysisRunRequest,
    db: Annotated[Session, Depends(get_db)],
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> dict[str, Any]:
    created = analysis.create_run(
        db,
        symbol=request.symbol,
        market_type=request.market_type,
        trigger=request.trigger,
        now=now(),
        idempotency_key=idempotency_key,
    )
    created["status_url"] = f"/api/v1/analysis/runs/{created['run_id']}"
    return success_payload(
        created, request_id=ctx.request_id, trace_id=ctx.trace_id, generated_at=now()
    )


@router.get("/runs/{run_id}", response_model=SuccessResponse[AnalysisRunSchema])
def get_analysis_run(
    run_id: str,
    db: Annotated[Session, Depends(get_db)],
    ctx: Annotated[RequestContext, Depends(get_request_context)],
) -> dict[str, Any]:
    data = analysis.get_run(db, run_id)
    data["status_url"] = f"/api/v1/analysis/runs/{run_id}"
    return success_payload(
        data, request_id=ctx.request_id, trace_id=ctx.trace_id, generated_at=now()
    )


@router.get("/latest", response_model=SuccessResponse[AnalysisReportSchema | None])
def get_latest_analysis(
    db: Annotated[Session, Depends(get_db)],
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    symbol: Annotated[str | None, Query(max_length=32)] = None,
    market_type: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    data = analysis.latest_report(db, symbol=symbol, market_type=market_type)
    return success_payload(
        data, request_id=ctx.request_id, trace_id=ctx.trace_id, generated_at=now()
    )


@router.get("/{analysis_id}", response_model=SuccessResponse[AnalysisReportSchema])
def get_analysis_report(
    analysis_id: str,
    db: Annotated[Session, Depends(get_db)],
    ctx: Annotated[RequestContext, Depends(get_request_context)],
) -> dict[str, Any]:
    data = analysis.get_report(db, analysis_id)
    return success_payload(
        data, request_id=ctx.request_id, trace_id=ctx.trace_id, generated_at=now()
    )


@router.post("/{analysis_id}/feedback", response_model=SuccessResponse[AnalysisFeedbackResponse])
def add_analysis_feedback(
    analysis_id: str,
    request: AnalysisFeedbackRequest,
    db: Annotated[Session, Depends(get_db)],
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    data = analysis.add_feedback(
        db,
        analysis_id=analysis_id,
        useful=request.useful,
        reason=request.reason,
        idempotency_key=idempotency_key,
        now=now(),
    )
    return success_payload(
        data, request_id=ctx.request_id, trace_id=ctx.trace_id, generated_at=now()
    )
