"""Binance 公共 REST K 线的共享常量与纯函数（补数 / 校验脚本共用）。

与 ``scripts/backfill_market.py``、``scripts/verify_market_candles.py`` 配套。
本模块不做任何 IO，便于单元测试。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

EXCHANGE = "binance"

#: market_type -> (base_url, path)
ENDPOINTS: dict[str, tuple[str, str]] = {
    "SPOT": ("https://api.binance.com", "/api/v3/klines"),
    "USDM_FUTURES": ("https://fapi.binance.com", "/fapi/v1/klines"),
}

TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
}


def ms_to_dt(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def dec(value: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"非法数值: {value!r}") from exc


def parse_kline(
    raw: list[Any],
    *,
    symbol: str,
    market_type: str,
    timeframe: str,
    fetched_at: datetime,
    source: str,
) -> dict[str, Any]:
    """把 Binance kline 数组转换为 market_candles 行。

    Binance 返回格式：
    [openTime, open, high, low, close, volume, closeTime, quoteVolume, trades,
     takerBuyBase, takerBuyQuote, ignore]
    """
    open_time = ms_to_dt(int(raw[0]))
    close_time = ms_to_dt(int(raw[6]))
    return {
        "exchange": EXCHANGE,
        "market_type": market_type,
        "symbol": symbol,
        "timeframe": timeframe,
        "open_time": open_time,
        "close_time": close_time,
        "open": dec(raw[1]),
        "high": dec(raw[2]),
        "low": dec(raw[3]),
        "close": dec(raw[4]),
        "volume": dec(raw[5]),
        "quote_volume": dec(raw[7]),
        "trades_count": int(raw[8]),
        "taker_buy_base_volume": dec(raw[9]),
        "taker_buy_quote_volume": dec(raw[10]),
        "exchange_ts": close_time,
        "received_at": fetched_at,
        "processed_at": fetched_at,
        "is_closed": True,
        "source": source,
    }


def is_valid_ohlc(row: dict[str, Any]) -> bool:
    """与 ``ck_market_candles_ohlc`` / ``volume`` 约束保持一致的写前校验（30.2）。"""
    o, h, low, c = row["open"], row["high"], row["low"], row["close"]
    if h < max(o, c, low) or h < low:
        return False
    return row["volume"] >= 0 and row["quote_volume"] >= 0
