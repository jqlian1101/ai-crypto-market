"""行情只读用例；统一执行已收盘与时间截断规则。"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aicrypto.common.errors import AICryptoError, ErrorCode
from aicrypto.infrastructure.database.models.market import MarketCandle

ALLOWED_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h")

#: K 线周期 -> 秒数
_TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}


def _timeframe_seconds(timeframe: str) -> int:
    """K 线周期对应的秒数。"""
    try:
        return _TIMEFRAME_SECONDS[timeframe]
    except KeyError as exc:
        raise AICryptoError(
            ErrorCode.COMMON_VALIDATION_FAILED,
            message="timeframe 非法",
            details={"timeframe": timeframe, "allowed": list(ALLOWED_TIMEFRAMES)},
        ) from exc


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


def price_change_pct(
    db: Session,
    *,
    symbol: str,
    market_type: str,
    timeframe: str,
    window_seconds: int,
    as_of: datetime,
) -> float | None:
    """已收盘 K 线收盘价的变化率（百分比）。

    用于衍生品快照里「价格 vs 持仓量」的联合判定（22.2.4
    ``DER_PRICE_OI_DIVERGENCE`` / ``DER_LS_SKEW``）。

    **只使用已收盘 K 线**（29.5）：未收盘 K 线参与判定会产生假异动。

    **按根数而非连续时间窗取数**：K 线是离散聚合，用「``close_time`` 落在
    ``[as_of - window, as_of]``」这种连续窗口去切，会在窗口边界上丢数据——
    例如 1h 周期、窗口 1h 时，最新那根的 ``close_time`` 恰好等于
    ``as_of - 1h``，被严格大于号排除，只剩一根就无法比较。
    因此这里换算成**需要的根数**：``窗口 / 周期 + 1`` 根，取其中最老与最新
    两根比较。根数不足时返回 ``None``——调用方据此判定「无法计算」，
    而不是用 0 冒充。

    **与 OI 口径对齐**：领域层 ``oi_change_pct()`` 的窗口起点是
    「``<= 最新点 - window`` 的最后一个采样点」，终点是最新点。
    本函数同样以**最新已收盘 K 线**为终点，往前取够一个完整窗口的那根，
    保证「价格变化率」与「持仓量变化率」覆盖的是同一段时间——否则
    两者量纲不同，背离判定没有意义。

    价格用 ``Decimal`` 直接算，全程不经过浮点中转（30.2 的口径）。
    """
    symbol = symbol.strip().upper()
    market_type = normalize_market_type(market_type)
    timeframe = timeframe.strip().lower()
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise AICryptoError(
            ErrorCode.COMMON_VALIDATION_FAILED,
            message="timeframe 非法",
            details={"timeframe": timeframe, "allowed": list(ALLOWED_TIMEFRAMES)},
        )
    period = _timeframe_seconds(timeframe)
    if window_seconds <= 0 or period <= 0:
        raise AICryptoError(
            ErrorCode.COMMON_VALIDATION_FAILED,
            message="window_seconds 必须为正",
            details={"window_seconds": window_seconds},
        )
    # 需要的根数：覆盖整个窗口 + 起点那根，才能算出窗口两端的变化
    needed = window_seconds // period + 1
    rows = list(
        db.execute(
            select(MarketCandle)
            .where(*_closed_filters(symbol, market_type, timeframe, as_of))
            .order_by(MarketCandle.close_time.desc())
            .limit(needed)
        ).scalars()
    )
    # 至少要两根才能比较：窗口起点那根与最新那根
    if len(rows) < 2:
        return None
    # 查询是倒序（最新在前），所以最新的是 rows[0]，最老的是 rows[-1]
    base = rows[-1].close
    latest = rows[0].close
    if base == 0:
        return None
    # 百分比：(latest - base) / base * 100
    change = (latest - base) / base * Decimal("100")
    return float(change)


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
