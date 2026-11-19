"""行情 HTTP 接入；查询及时间截断规则位于 services.market。"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from aicrypto.api.auth import require_auth
from aicrypto.api.dependencies import RequestContext, get_request_context
from aicrypto.api.schemas.market import CandleSeries, MarketOverview
from aicrypto.common.clock import now
from aicrypto.common.responses import SuccessResponse, success_payload
from aicrypto.infrastructure.database.session import get_db
from aicrypto.services import market

router = APIRouter(prefix="/market", dependencies=[Depends(require_auth)])


@router.get("/overview", summary="单资产多周期概览", response_model=SuccessResponse[MarketOverview])
def market_overview(
    db: Annotated[Session, Depends(get_db)],
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    symbol: Annotated[str, Query(min_length=1, max_length=32, description="交易对，如 BTCUSDT")],
    market_type: Annotated[str, Query(description="SPOT / USDM_FUTURES")] = "SPOT",
) -> dict[str, Any]:
    cutoff = now()
    data = market.market_overview(db, symbol=symbol, market_type=market_type, as_of=cutoff)
    return success_payload(
        data, request_id=ctx.request_id, trace_id=ctx.trace_id, generated_at=cutoff
    )


@router.get("/candles", summary="K 线序列", response_model=SuccessResponse[CandleSeries])
def list_candles(
    db: Annotated[Session, Depends(get_db)],
    ctx: Annotated[RequestContext, Depends(get_request_context)],
    symbol: Annotated[str, Query(min_length=1, max_length=32)],
    timeframe: Annotated[str, Query(description="1m / 5m / 15m / 1h / 4h")],
    market_type: Annotated[str, Query()] = "SPOT",
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict[str, Any]:
    cutoff = now()
    data = market.list_candles(
        db,
        symbol=symbol,
        timeframe=timeframe,
        market_type=market_type,
        limit=limit,
        as_of=cutoff,
    )
    return success_payload(
        data, request_id=ctx.request_id, trace_id=ctx.trace_id, generated_at=cutoff
    )


__all__ = ["router"]
