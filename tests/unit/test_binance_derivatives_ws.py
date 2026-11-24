"""衍生品与盘口 WS 流的纯函数测试（PRD 29.2 / 30.2）。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from aicrypto.infrastructure.binance_derivatives_ws import (
    MAX_STREAMS_PER_CONNECTION,
    agg_trade_stream_names,
    build_stream_url,
    mark_price_stream_names,
    parse_agg_trade_message,
    parse_mark_price_message,
)

UTC = dt.UTC
RECEIVED = dt.datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)


def test_agg_trade_stream_names_lowercase_and_ordered() -> None:
    assert agg_trade_stream_names(["BTCUSDT", "ETHUSDT"]) == [
        "btcusdt@aggTrade",
        "ethusdt@aggTrade",
    ]


def test_mark_price_stream_names_use_1s_interval() -> None:
    assert mark_price_stream_names(["BTCUSDT"]) == ["btcusdt@markPrice@1s"]


def test_build_stream_url_per_market() -> None:
    spot = build_stream_url("SPOT", ["btcusdt@aggTrade"])
    fut = build_stream_url("USDM_FUTURES", ["btcusdt@aggTrade", "btcusdt@markPrice@1s"])
    assert spot == "wss://stream.binance.com:9443/stream?streams=btcusdt@aggTrade"
    assert fut.startswith("wss://fstream.binance.com/stream?streams=")


@pytest.mark.parametrize(
    ("market_type", "streams"),
    [
        ("COIN_FUTURES", ["btcusdt@aggTrade"]),
        ("SPOT", []),
        ("SPOT", [f"s{i}@aggTrade" for i in range(MAX_STREAMS_PER_CONNECTION + 1)]),
    ],
)
def test_build_stream_url_rejects_invalid(market_type: str, streams: list[str]) -> None:
    with pytest.raises(ValueError):
        build_stream_url(market_type, streams)


# --------------------------------------------------------------------------
# 聚合成交
# --------------------------------------------------------------------------


def make_agg_trade(**overrides: Any) -> dict[str, Any]:
    payload = {
        "e": "aggTrade",
        "E": 1767225600100,
        "s": "BTCUSDT",
        "a": 12345,
        "p": "42000.50",
        "q": "0.125",
        "f": 100,
        "l": 105,
        "T": 1767225600000,
        "m": False,
        "M": True,
    }
    payload.update(overrides)
    return {"stream": "btcusdt@aggTrade", "data": payload}


def test_parse_agg_trade_maps_fields() -> None:
    event = parse_agg_trade_message(
        make_agg_trade(), market_type="USDM_FUTURES", received_at=RECEIVED
    )
    assert event is not None
    assert event.symbol == "BTCUSDT"
    assert event.trade_id == 12345
    assert event.price == Decimal("42000.50")
    assert event.quantity == Decimal("0.125")
    assert event.quote_quantity == Decimal("42000.50") * Decimal("0.125")
    assert event.exchange_ts == dt.datetime(2026, 1, 1, tzinfo=UTC)


def test_parse_agg_trade_taker_buy_direction() -> None:
    """30.2 / 4.5：``is_taker_buy = NOT is_buyer_maker``。

    方向搞反会让全部流动性分析结论反转，因此单独固定。
    """
    maker_buy = parse_agg_trade_message(
        make_agg_trade(m=True), market_type="SPOT", received_at=RECEIVED
    )
    assert maker_buy is not None
    # Binance: m=true 表示买方是挂单方 => 主动卖出
    assert maker_buy.is_taker_buy is False

    maker_sell = parse_agg_trade_message(
        make_agg_trade(m=False), market_type="SPOT", received_at=RECEIVED
    )
    assert maker_sell is not None
    assert maker_sell.is_taker_buy is True


def test_parse_agg_trade_row_shape() -> None:
    event = parse_agg_trade_message(
        make_agg_trade(), market_type="SPOT", received_at=RECEIVED
    )
    assert event is not None
    row = event.row()
    assert set(row) == {
        "exchange",
        "market_type",
        "symbol",
        "trade_id",
        "price",
        "quantity",
        "quote_quantity",
        "is_taker_buy",
        "exchange_ts",
        "received_at",
    }
    assert row["exchange"] == "binance"
    assert row["received_at"] == RECEIVED


def test_parse_agg_trade_missing_field_raises() -> None:
    message = make_agg_trade()
    del message["data"]["p"]
    with pytest.raises(ValueError, match="字段缺失"):
        parse_agg_trade_message(message, market_type="SPOT", received_at=RECEIVED)


@pytest.mark.parametrize(
    "message",
    [{"result": None}, {"data": {"e": "kline"}}, {"data": "x"}],
)
def test_parse_non_agg_trade_returns_none(message: dict[str, Any]) -> None:
    assert parse_agg_trade_message(message, market_type="SPOT", received_at=RECEIVED) is None


# --------------------------------------------------------------------------
# 标记价格
# --------------------------------------------------------------------------


def make_mark_price(**overrides: Any) -> dict[str, Any]:
    payload = {
        "e": "markPriceUpdate",
        "E": 1767225600100,
        "s": "BTCUSDT",
        "p": "42000.00",
        "i": "41990.00",
        "r": "0.00010000",
        "T": 1767254400000,
    }
    payload.update(overrides)
    return {"stream": "btcusdt@markPrice@1s", "data": payload}


def test_parse_mark_price_maps_fields() -> None:
    event = parse_mark_price_message(
        make_mark_price(), market_type="USDM_FUTURES", received_at=RECEIVED
    )
    assert event is not None
    assert event.symbol == "BTCUSDT"
    assert event.mark_price == Decimal("42000.00")
    assert event.index_price == Decimal("41990.00")
    assert event.funding_rate == Decimal("0.00010000")
    assert event.next_funding_time == dt.datetime(2026, 1, 1, 8, tzinfo=UTC)


def test_parse_mark_price_missing_index_is_none() -> None:
    event = parse_mark_price_message(
        make_mark_price(i=None), market_type="USDM_FUTURES", received_at=RECEIVED
    )
    assert event is not None
    assert event.index_price is None


def test_parse_mark_price_missing_field_raises() -> None:
    message = make_mark_price()
    del message["data"]["r"]
    with pytest.raises(ValueError, match="字段缺失"):
        parse_mark_price_message(message, market_type="USDM_FUTURES", received_at=RECEIVED)


def test_parse_non_mark_price_returns_none() -> None:
    assert (
        parse_mark_price_message({"data": {"e": "aggTrade"}}, market_type="SPOT", received_at=RECEIVED)
        is None
    )
