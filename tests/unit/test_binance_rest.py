"""Binance REST K 线共享纯函数测试（补数 / 校验脚本依赖）。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from aicrypto.infrastructure.binance_rest import (
    ENDPOINTS,
    TIMEFRAME_MS,
    dec,
    is_valid_ohlc,
    parse_kline,
)

UTC = dt.UTC
OPEN_MS = 1_700_000_000_000  # 2023-11-14 22:13:20 UTC
INTERVAL_MS = TIMEFRAME_MS["1h"]


def make_raw(
    *,
    open_: str = "100",
    high: str = "105",
    low: str = "95",
    close: str = "102",
    volume: str = "10",
    quote_volume: str = "1000",
) -> list[str | int]:
    return [
        OPEN_MS,
        open_,
        high,
        low,
        close,
        volume,
        OPEN_MS + INTERVAL_MS - 1,
        quote_volume,
        42,
        "4",
        "400",
        "0",
    ]


FETCHED = dt.datetime(2026, 1, 1, tzinfo=UTC)


def test_parse_kline_maps_fields() -> None:
    row = parse_kline(
        make_raw(),
        symbol="BTCUSDT",
        market_type="SPOT",
        timeframe="1h",
        fetched_at=FETCHED,
        source="REST_BACKFILL",
    )
    assert row["exchange"] == "binance"
    assert row["open_time"] == dt.datetime.fromtimestamp(OPEN_MS / 1000, tz=UTC)
    assert row["close_time"] == row["open_time"] + dt.timedelta(milliseconds=INTERVAL_MS - 1)
    assert row["open"] == Decimal("100")
    assert row["high"] == Decimal("105")
    assert row["trades_count"] == 42
    assert row["taker_buy_base_volume"] == Decimal("4")
    assert row["is_closed"] is True
    assert row["source"] == "REST_BACKFILL"


def test_parse_kline_uses_given_source() -> None:
    row = parse_kline(
        make_raw(),
        symbol="ETHUSDT",
        market_type="USDM_FUTURES",
        timeframe="1h",
        fetched_at=FETCHED,
        source="OTHER",
    )
    assert row["source"] == "OTHER"
    assert row["market_type"] == "USDM_FUTURES"


def test_dec_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="非法数值"):
        dec("abc")


def test_dec_rejects_none() -> None:
    with pytest.raises(ValueError, match="非法数值"):
        dec(None)  # type: ignore[arg-type]


def _row(**overrides: Decimal) -> dict[str, Decimal]:
    base = {
        "open": Decimal("100"),
        "high": Decimal("105"),
        "low": Decimal("95"),
        "close": Decimal("102"),
        "volume": Decimal("10"),
        "quote_volume": Decimal("1000"),
    }
    base.update(overrides)
    return base


def test_valid_ohlc_accepted() -> None:
    assert is_valid_ohlc(_row()) is True


def test_high_below_open_rejected() -> None:
    assert is_valid_ohlc(_row(high=Decimal("99"))) is False


def test_high_below_low_rejected() -> None:
    assert is_valid_ohlc(_row(high=Decimal("90"), low=Decimal("95"))) is False


def test_negative_volume_rejected() -> None:
    assert is_valid_ohlc(_row(volume=Decimal("-1"))) is False


def test_negative_quote_volume_rejected() -> None:
    assert is_valid_ohlc(_row(quote_volume=Decimal("-1"))) is False


def test_endpoints_cover_all_market_types() -> None:
    assert set(ENDPOINTS) == {"SPOT", "USDM_FUTURES"}
    assert set(TIMEFRAME_MS) == {"1m", "5m", "15m", "1h", "4h"}
