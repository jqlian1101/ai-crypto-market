"""Binance WS 纯函数测试（PRD 29.2 / 29.3 / 29.5 / 29.8）。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from aicrypto.infrastructure.binance_ws import (
    MAX_STREAMS_PER_CONNECTION,
    build_stream_url,
    parse_kline_message,
    reconnect_delay_seconds,
    stream_names,
)

UTC = dt.UTC
RECEIVED = dt.datetime(2026, 1, 1, 0, 5, 1, tzinfo=UTC)
OPEN_MS = 1_767_225_600_000  # 2026-01-01 00:00:00 UTC


def make_message(*, closed: bool = True, **k_overrides: Any) -> dict[str, Any]:
    k: dict[str, Any] = {
        "t": OPEN_MS,
        "T": OPEN_MS + 300_000 - 1,
        "s": "BTCUSDT",
        "i": "5m",
        "o": "100.10",
        "c": "101.20",
        "h": "102.00",
        "l": "99.50",
        "v": "12.5",
        "n": 321,
        "x": closed,
        "q": "1260.00",
        "V": "6.1",
        "Q": "615.00",
    }
    k.update(k_overrides)
    return {
        "stream": "btcusdt@kline_5m",
        "data": {"e": "kline", "E": OPEN_MS + 300_100, "s": "BTCUSDT", "k": k},
    }


def test_stream_names_lowercase_and_ordered() -> None:
    assert stream_names(["BTCUSDT", "ETHUSDT"], ["5m", "1h"]) == [
        "btcusdt@kline_5m",
        "btcusdt@kline_1h",
        "ethusdt@kline_5m",
        "ethusdt@kline_1h",
    ]


def test_build_stream_url_separate_hosts_per_market() -> None:
    spot = build_stream_url("SPOT", ["btcusdt@kline_5m"])
    fut = build_stream_url("USDM_FUTURES", ["btcusdt@kline_5m", "ethusdt@kline_5m"])
    assert spot == "wss://stream.binance.com:9443/stream?streams=btcusdt@kline_5m"
    assert fut.startswith("wss://fstream.binance.com/stream?streams=")
    assert fut.endswith("btcusdt@kline_5m/ethusdt@kline_5m")


@pytest.mark.parametrize(
    ("market_type", "streams"),
    [
        ("COIN_FUTURES", ["btcusdt@kline_5m"]),
        ("SPOT", []),
        ("SPOT", [f"s{i}@kline_5m" for i in range(MAX_STREAMS_PER_CONNECTION + 1)]),
    ],
)
def test_build_stream_url_rejects_invalid(market_type: str, streams: list[str]) -> None:
    with pytest.raises(ValueError):
        build_stream_url(market_type, streams)


def test_parse_closed_kline_maps_fields_and_timestamps() -> None:
    event = parse_kline_message(make_message(), market_type="USDM_FUTURES", received_at=RECEIVED)
    assert event is not None
    assert event.is_closed is True
    row = event.row
    assert row["market_type"] == "USDM_FUTURES"
    assert row["symbol"] == "BTCUSDT"
    assert row["timeframe"] == "5m"
    assert row["open_time"] == dt.datetime(2026, 1, 1, tzinfo=UTC)
    assert row["close"] == Decimal("101.20")
    assert row["trades_count"] == 321
    assert row["taker_buy_quote_volume"] == Decimal("615.00")
    assert row["source"] == "WS"
    # 29.8：exchange_ts 取事件 E，received_at 为本地到达时刻
    assert row["exchange_ts"] == dt.datetime(2026, 1, 1, 0, 5, 0, 100_000, tzinfo=UTC)
    assert row["received_at"] == RECEIVED


def test_parse_unclosed_kline_flagged() -> None:
    event = parse_kline_message(
        make_message(closed=False), market_type="SPOT", received_at=RECEIVED
    )
    assert event is not None
    assert event.is_closed is False
    assert event.row["is_closed"] is False


def test_parse_accepts_raw_stream_payload_without_wrapper() -> None:
    event = parse_kline_message(make_message()["data"], market_type="SPOT", received_at=RECEIVED)
    assert event is not None


@pytest.mark.parametrize(
    "message",
    [{"result": None, "id": 1}, {"data": {"e": "aggTrade"}}, {"data": "x"}],
)
def test_parse_non_kline_returns_none(message: dict[str, Any]) -> None:
    assert parse_kline_message(message, market_type="SPOT", received_at=RECEIVED) is None


def test_parse_missing_field_raises() -> None:
    message = make_message()
    del message["data"]["k"]["c"]
    with pytest.raises(ValueError, match="字段缺失"):
        parse_kline_message(message, market_type="SPOT", received_at=RECEIVED)


def test_parse_bad_number_raises() -> None:
    with pytest.raises(ValueError, match="非法数值"):
        parse_kline_message(make_message(c="abc"), market_type="SPOT", received_at=RECEIVED)


def test_reconnect_delay_exponential_with_cap() -> None:
    no_jitter = lambda: 0.0  # noqa: E731
    assert reconnect_delay_seconds(0, jitter=no_jitter) == 0.5
    assert reconnect_delay_seconds(1, jitter=no_jitter) == 1.0
    assert reconnect_delay_seconds(5, jitter=no_jitter) == 16.0
    assert reconnect_delay_seconds(6, jitter=no_jitter) == 30.0
    assert reconnect_delay_seconds(10_000, jitter=no_jitter) == 30.0


def test_reconnect_delay_jitter_bounded() -> None:
    assert reconnect_delay_seconds(0, jitter=lambda: 0.999) == pytest.approx(0.9995)
    assert reconnect_delay_seconds(3, max_ms=1000, jitter=lambda: 0.5) == pytest.approx(1.25)


def test_reconnect_delay_rejects_negative_attempt() -> None:
    with pytest.raises(ValueError):
        reconnect_delay_seconds(-1)
