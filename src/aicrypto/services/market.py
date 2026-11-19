"""行情只读用例；统一执行已收盘与时间截断规则。"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aicrypto.common.errors import AICryptoError, ErrorCode
from aicrypto.infrastructure.database.models.market import MarketCandle

ALLOWED_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h")


def normalize_market_type(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in ("SPOT", "USDM_FUTURES"):
        raise AICryptoError(
            ErrorCode.COMMON_VALIDATION_FAILED,
            message="market_type 非法",
            details={"market_type": value, "allowed": ["SPOT", "USDM_FUTURES"]},
        )
    return normalized


def _closed_filters(
    symbol: str, market_type: str, timeframe: str, as_of: datetime
) -> tuple[Any, ...]:
    return (
        MarketCandle.symbol == symbol,
        MarketCandle.market_type == market_type,
        MarketCandle.timeframe == timeframe,
        MarketCandle.is_closed.is_(True),
        MarketCandle.close_time <= as_of,
    )


def candle_payload(candle: MarketCandle) -> dict[str, Any]:
    """价格、数量以十进制字符串传输，不经浮点中转。"""
    return {
        "open_time": candle.open_time.isoformat(),
        "close_time": candle.close_time.isoformat(),
        **{
            field: str(getattr(candle, field))
            for field in ("open", "high", "low", "close", "volume", "quote_volume")
        },
        "trades_count": candle.trades_count,
        "source": candle.source,
    }


def market_overview(
    db: Session, *, symbol: str, market_type: str, as_of: datetime
) -> dict[str, Any]:
    symbol = symbol.strip().upper()
    market_type = normalize_market_type(market_type)
    frames = []
    for timeframe in ("5m", "15m", "1h", "4h"):
        filters = _closed_filters(symbol, market_type, timeframe, as_of)
        candle = (
            db.execute(
                select(MarketCandle)
                .where(*filters)
                .order_by(MarketCandle.close_time.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        count = (
            0
            if candle is None
            else int(
                db.execute(
                    select(func.count()).select_from(MarketCandle).where(*filters)
                ).scalar_one()
            )
        )
        frames.append(
            {
                "timeframe": timeframe,
                "latest": None if candle is None else candle_payload(candle),
                "candle_count": count,
            }
        )
    return {
        "symbol": symbol,
        "market_type": market_type,
        "as_of": as_of.isoformat(),
        "timeframes": frames,
    }


def list_candles(
    db: Session,
    *,
    symbol: str,
    market_type: str,
    timeframe: str,
    limit: int,
    as_of: datetime,
) -> dict[str, Any]:
    symbol = symbol.strip().upper()
    market_type = normalize_market_type(market_type)
    timeframe = timeframe.strip().lower()
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise AICryptoError(
            ErrorCode.COMMON_VALIDATION_FAILED,
            message="timeframe 非法",
            details={"timeframe": timeframe, "allowed": list(ALLOWED_TIMEFRAMES)},
        )
    if not 1 <= limit <= 1000:
        raise AICryptoError(ErrorCode.COMMON_VALIDATION_FAILED, message="limit 必须在 1–1000 之间")
    rows = list(
        db.execute(
            select(MarketCandle)
            .where(*_closed_filters(symbol, market_type, timeframe, as_of))
            .order_by(MarketCandle.close_time.desc())
            .limit(limit)
        ).scalars()
    )
    return {
        "symbol": symbol,
        "market_type": market_type,
        "timeframe": timeframe,
        "candles": [candle_payload(row) for row in reversed(rows)],
    }
