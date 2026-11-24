"""Binance 衍生品 WebSocket 流的纯函数部分（PRD 29.2 / 30.2）。

覆盖两条流：

- ``aggTrade`` —— 聚合成交，写入 ``market_trades``；
- ``markPrice@1s`` —— 标记价格与**当前预估**资金费率（仅 USDⓈ-M 有）。

本模块不做任何 IO：流名构造、URL 拼装、消息解析都是纯函数。
连接与收发在 :mod:`aicrypto.services.derivatives_collector` 中完成。

**方向语义固定**（30.2 / 4.5）：Binance 的 ``m=true`` 表示「买方是挂单方」，
即该笔为**主动卖出**。本项目统一存 ``is_taker_buy = NOT m``。
搞反会让全部流动性分析结论反转，因此有专门的单测。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from aicrypto.infrastructure.binance_rest import EXCHANGE, dec, ms_to_dt

#: market_type -> 组合流基础地址。衍生品流只在 USDⓈ-M 上存在。
WS_BASE_URLS: dict[str, str] = {
    "SPOT": "wss://stream.binance.com:9443/stream",
    "USDM_FUTURES": "wss://fstream.binance.com/stream",
}

#: 单连接订阅流数上限，与 K 线采集一致
MAX_STREAMS_PER_CONNECTION = 200

#: markPrice 流的推送频率（毫秒）。1s 是 Binance 支持的档位。
MARK_PRICE_UPDATE_MS = 1000


def agg_trade_stream_names(symbols: Sequence[str]) -> list[str]:
    """``btcusdt@aggTrade`` 形式的流名列表，顺序确定。"""
    return [f"{s.lower()}@aggTrade" for s in symbols]


def mark_price_stream_names(symbols: Sequence[str]) -> list[str]:
    """``btcusdt@markPrice@1s`` 形式的流名列表，顺序确定。"""
    return [f"{s.lower()}@markPrice@1s" for s in symbols]


def build_stream_url(market_type: str, streams: Sequence[str]) -> str:
    """构造组合流 URL。"""
    if market_type not in WS_BASE_URLS:
        raise ValueError(f"market_type 非法: {market_type!r}")
    if not streams:
        raise ValueError("订阅流列表为空")
    if len(streams) > MAX_STREAMS_PER_CONNECTION:
        raise ValueError(f"单连接订阅流数 {len(streams)} 超过上限 {MAX_STREAMS_PER_CONNECTION}")
    return f"{WS_BASE_URLS[market_type]}?streams={'/'.join(streams)}"


@dataclass(frozen=True, slots=True)
class AggTradeEvent:
    """一条聚合成交事件。"""

    market_type: str
    symbol: str
    trade_id: int
    price: Any
    quantity: Any
    quote_quantity: Any
    is_taker_buy: bool
    exchange_ts: dt.datetime
    received_at: dt.datetime

    def row(self) -> dict[str, Any]:
        """转换为 ``market_trades`` 行。"""
        return {
            "exchange": EXCHANGE,
            "market_type": self.market_type,
            "symbol": self.symbol,
            "trade_id": self.trade_id,
            "price": self.price,
            "quantity": self.quantity,
            "quote_quantity": self.quote_quantity,
            "is_taker_buy": self.is_taker_buy,
            "exchange_ts": self.exchange_ts,
            "received_at": self.received_at,
        }


@dataclass(frozen=True, slots=True)
class MarkPriceEvent:
    """一条标记价格事件（含当前预估资金费率）。"""

    market_type: str
    symbol: str
    mark_price: Any
    index_price: Any | None
    funding_rate: Any
    next_funding_time: dt.datetime | None
    exchange_ts: dt.datetime
    received_at: dt.datetime


def parse_agg_trade_message(
    message: dict[str, Any],
    *,
    market_type: str,
    received_at: dt.datetime,
) -> AggTradeEvent | None:
    """解析聚合成交消息。非 ``aggTrade`` 消息返回 ``None``。"""
    data = message.get("data", message)
    if not isinstance(data, dict) or data.get("e") != "aggTrade":
        return None
    try:
        price = dec(str(data["p"]))
        quantity = dec(str(data["q"]))
        symbol = str(data["s"]).upper()
        trade_id = int(data["a"])
        exchange_ts = ms_to_dt(int(data["T"]))
        is_buyer_maker = bool(data["m"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"聚合成交消息字段缺失: {exc}") from exc
    return AggTradeEvent(
        market_type=market_type,
        symbol=symbol,
        trade_id=trade_id,
        price=price,
        quantity=quantity,
        quote_quantity=price * quantity,
        # 30.2：is_taker_buy = NOT is_buyer_maker
        is_taker_buy=not is_buyer_maker,
        exchange_ts=exchange_ts,
        received_at=received_at,
    )


def parse_mark_price_message(
    message: dict[str, Any],
    *,
    market_type: str,
    received_at: dt.datetime,
) -> MarkPriceEvent | None:
    """解析标记价格消息。非 ``markPriceUpdate`` 消息返回 ``None``。"""
    data = message.get("data", message)
    if not isinstance(data, dict) or data.get("e") != "markPriceUpdate":
        return None
    try:
        symbol = str(data["s"]).upper()
        mark_price = dec(str(data["p"]))
        funding_rate = dec(str(data["r"]))
        exchange_ts = ms_to_dt(int(data["E"]))
    except (KeyError, TypeError) as exc:
        raise ValueError(f"标记价格消息字段缺失: {exc}") from exc
    index_raw = data.get("i")
    next_funding = data.get("T")
    return MarkPriceEvent(
        market_type=market_type,
        symbol=symbol,
        mark_price=mark_price,
        index_price=None if index_raw is None else dec(str(index_raw)),
        funding_rate=funding_rate,
        next_funding_time=None if next_funding is None else ms_to_dt(int(next_funding)),
        exchange_ts=exchange_ts,
        received_at=received_at,
    )


__all__ = [
    "MARK_PRICE_UPDATE_MS",
    "MAX_STREAMS_PER_CONNECTION",
    "WS_BASE_URLS",
    "AggTradeEvent",
    "MarkPriceEvent",
    "agg_trade_stream_names",
    "build_stream_url",
    "mark_price_stream_names",
    "parse_agg_trade_message",
    "parse_mark_price_message",
]
