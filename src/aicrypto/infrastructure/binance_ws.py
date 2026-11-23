"""Binance WebSocket K 线流的纯函数部分（PRD 29.2 / 29.3 / 29.5）。

本模块不做任何 IO：订阅 URL 构造、事件解析、重连退避计算都可直接单元测试。
连接与收发在 :mod:`aicrypto.services.market_collector` 中完成。
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aicrypto.infrastructure.binance_rest import EXCHANGE, dec, ms_to_dt

#: market_type -> 组合流基础地址。现货与永续是两套独立连接（29.2.1）。
WS_BASE_URLS: dict[str, str] = {
    "SPOT": "wss://stream.binance.com:9443/stream",
    "USDM_FUTURES": "wss://fstream.binance.com/stream",
}

#: 单连接订阅流数上限（Binance 限制 1024，留余量）
MAX_STREAMS_PER_CONNECTION = 200

#: 半开连接判定：连续多少秒无任何消息（29.3.1）
HALF_OPEN_TIMEOUT_SECONDS = 30.0

#: 单连接最大存活。Binance 24h 主动断开，提前 5 分钟自行轮换以控制恢复时机。
MAX_CONNECTION_AGE_SECONDS = 24 * 3600 - 300


def stream_names(symbols: Sequence[str], timeframes: Sequence[str]) -> list[str]:
    """``btcusdt@kline_5m`` 形式的流名列表，顺序确定。"""
    return [f"{s.lower()}@kline_{tf}" for s in symbols for tf in timeframes]


def build_stream_url(market_type: str, streams: Sequence[str]) -> str:
    if market_type not in WS_BASE_URLS:
        raise ValueError(f"market_type 非法: {market_type!r}")
    if not streams:
        raise ValueError("订阅流列表为空")
    if len(streams) > MAX_STREAMS_PER_CONNECTION:
        raise ValueError(f"单连接订阅流数 {len(streams)} 超过上限 {MAX_STREAMS_PER_CONNECTION}")
    return f"{WS_BASE_URLS[market_type]}?streams={'/'.join(streams)}"


def reconnect_delay_seconds(
    attempt: int,
    *,
    max_ms: int = 30_000,
    jitter: Callable[[], float] = random.random,
) -> float:
    """指数退避 + 抖动：``min(2^attempt × 500ms, max) + random(0, 500ms)``（29.3.1）。

    ``attempt`` 从 0 开始；``jitter`` 返回 [0, 1)，可注入以便测试。
    """
    if attempt < 0:
        raise ValueError("attempt 必须 >= 0")
    # 先封顶指数再相乘，避免 attempt 很大时整数溢出成巨大数字
    base_ms = min(500 * (2 ** min(attempt, 16)), max_ms)
    return (base_ms + jitter() * 500) / 1000


@dataclass(frozen=True, slots=True)
class KlineEvent:
    """一条 WS K 线事件。``is_closed`` 对应 ``k.x``。"""

    market_type: str
    symbol: str
    timeframe: str
    is_closed: bool
    exchange_ts: datetime
    row: dict[str, Any]


def parse_kline_message(
    message: dict[str, Any],
    *,
    market_type: str,
    received_at: datetime,
) -> KlineEvent | None:
    """解析组合流消息。非 K 线消息返回 ``None``；字段缺失或非法时抛 ``ValueError``。

    ``row`` 的字段与 ``market_candles`` 一致，``source="WS"``，
    ``processed_at`` 先填 ``received_at``，入库时再覆盖为真实处理时刻（29.8）。
    """
    data = message.get("data", message)
    if not isinstance(data, dict) or data.get("e") != "kline":
        return None
    try:
        k = data["k"]
        symbol = str(k["s"]).upper()
        timeframe = str(k["i"])
        open_time = ms_to_dt(int(k["t"]))
        close_time = ms_to_dt(int(k["T"]))
        exchange_ts = ms_to_dt(int(data["E"]))
        row: dict[str, Any] = {
            "exchange": EXCHANGE,
            "market_type": market_type,
            "symbol": symbol,
            "timeframe": timeframe,
            "open_time": open_time,
            "close_time": close_time,
            "open": dec(k["o"]),
            "high": dec(k["h"]),
            "low": dec(k["l"]),
            "close": dec(k["c"]),
            "volume": dec(k["v"]),
            "quote_volume": dec(k["q"]),
            "trades_count": int(k["n"]),
            "taker_buy_base_volume": dec(k["V"]),
            "taker_buy_quote_volume": dec(k["Q"]),
            "exchange_ts": exchange_ts,
            "received_at": received_at,
            "processed_at": received_at,
            "is_closed": bool(k["x"]),
            "source": "WS",
        }
    except (KeyError, TypeError) as exc:
        raise ValueError(f"K 线消息字段缺失: {exc}") from exc
    return KlineEvent(
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        is_closed=row["is_closed"],
        exchange_ts=exchange_ts,
        row=row,
    )


__all__ = [
    "HALF_OPEN_TIMEOUT_SECONDS",
    "MAX_CONNECTION_AGE_SECONDS",
    "MAX_STREAMS_PER_CONNECTION",
    "WS_BASE_URLS",
    "KlineEvent",
    "build_stream_url",
    "parse_kline_message",
    "reconnect_delay_seconds",
    "stream_names",
]
