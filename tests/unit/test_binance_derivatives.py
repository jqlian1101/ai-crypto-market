"""Binance 衍生品 REST 端点与解析的纯函数测试（PRD 27.2 / 29.2 / 30.2）。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from aicrypto.infrastructure.binance_derivatives import (
    depth_weight,
    endpoint_weight,
    exchange_info_weight,
    parse_depth_snapshot,
    parse_funding_rate,
    parse_instrument_metadata,
    parse_instrument_metadata_list,
    parse_open_interest,
    parse_open_interest_history,
    parse_premium_index,
    spread_bps,
)

UTC = dt.UTC
CAPTURED_AT = dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# 权重
# --------------------------------------------------------------------------


def test_depth_weight_matches_binance_table() -> None:
    # limit <= 100 两市场均为 2
    assert depth_weight("SPOT", 20) == 2
    assert depth_weight("USDM_FUTURES", 20) == 2
    # 500 档：合约 5，现货 10
    assert depth_weight("USDM_FUTURES", 500) == 5
    assert depth_weight("SPOT", 500) == 10
    # 1000 档：合约 10，现货 20
    assert depth_weight("USDM_FUTURES", 1000) == 10
    assert depth_weight("SPOT", 1000) == 20


def test_endpoint_weight_low_weight_endpoints() -> None:
    for endpoint in (
        "open_interest",
        "open_interest_history",
        "funding_rate",
        "premium_index",
    ):
        assert endpoint_weight(endpoint, market_type="USDM_FUTURES") == 1


def test_endpoint_weight_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="未知接口"):
        endpoint_weight("nonexistent", market_type="SPOT")


def test_exchange_info_weight_differs_by_market() -> None:
    assert exchange_info_weight("USDM_FUTURES") == 1
    assert exchange_info_weight("SPOT") == 20


# --------------------------------------------------------------------------
# Open Interest
# --------------------------------------------------------------------------


def test_parse_open_interest_maps_fields() -> None:
    row = parse_open_interest(
        {"symbol": "btcusdt", "openInterest": "12345.678", "time": 1767225600000}
    )
    assert row["symbol"] == "BTCUSDT"
    assert row["open_interest"] == Decimal("12345.678")
    assert row["ts"] == dt.datetime(2026, 1, 1, tzinfo=UTC)
    assert row["market_type"] == "USDM_FUTURES"
    # 计价币价值缺失时留空，不填 0
    assert row["open_interest_value"] is None


def test_parse_open_interest_history_sorted_ascending() -> None:
    rows = parse_open_interest_history(
        [
            {
                "symbol": "BTCUSDT",
                "sumOpenInterest": "200",
                "sumOpenInterestValue": "1.0",
                "timestamp": 1767225600000,
            },
            {
                "symbol": "BTCUSDT",
                "sumOpenInterest": "100",
                "sumOpenInterestValue": "0.5",
                "timestamp": 1767222000000,
            },
        ]
    )
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)
    assert rows[0]["open_interest"] == Decimal("100")
    assert rows[0]["open_interest_value"] == Decimal("0.5")


# --------------------------------------------------------------------------
# Funding
# --------------------------------------------------------------------------


def test_parse_funding_rate_maps_fields() -> None:
    row = parse_funding_rate(
        {
            "symbol": "BTCUSDT",
            "fundingRate": "0.00010000",
            "fundingTime": 1767225600000,
            "markPrice": "42000.50",
        }
    )
    assert row["funding_rate"] == Decimal("0.00010000")
    assert row["mark_price"] == Decimal("42000.50")
    assert row["funding_time"] == dt.datetime(2026, 1, 1, tzinfo=UTC)


def test_parse_funding_rate_missing_mark_price_is_none() -> None:
    row = parse_funding_rate(
        {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingTime": 1767225600000}
    )
    assert row["mark_price"] is None


def test_parse_premium_index_maps_fields() -> None:
    row = parse_premium_index(
        {
            "symbol": "BTCUSDT",
            "markPrice": "42000.00",
            "indexPrice": "41990.00",
            "lastFundingRate": "0.0001",
            "nextFundingTime": 1767254400000,
            "time": 1767225600000,
        }
    )
    assert row["mark_price"] == Decimal("42000.00")
    assert row["index_price"] == Decimal("41990.00")
    assert row["last_funding_rate"] == Decimal("0.0001")
    assert row["next_funding_time"] is not None


# --------------------------------------------------------------------------
# 深度快照
# --------------------------------------------------------------------------


def make_depth() -> dict[str, Any]:
    return {
        "lastUpdateId": 1,
        "E": 1767225600000,
        "T": 1767225600000,
        "bids": [["42000.00", "1.5"], ["41999.50", "2.0"]],
        "asks": [["42000.10", "0.9"], ["42000.60", "1.2"]],
    }


def test_parse_depth_snapshot_keeps_only_top_levels() -> None:
    row = parse_depth_snapshot(
        make_depth(), symbol="BTCUSDT", market_type="USDM_FUTURES", captured_at=CAPTURED_AT
    )
    assert len(row["bids"]) == 2
    assert len(row["asks"]) == 2
    assert row["best_bid"] == Decimal("42000.00")
    assert row["best_ask"] == Decimal("42000.10")
    assert row["bid_qty_sum"] == Decimal("3.5")
    assert row["ask_qty_sum"] == Decimal("2.1")
    assert row["exchange_ts"] == dt.datetime(2026, 1, 1, tzinfo=UTC)


def test_parse_depth_snapshot_truncates_to_requested_levels() -> None:
    raw = {
        "bids": [[str(42000 - i), "1"] for i in range(30)],
        "asks": [[str(42010 + i), "1"] for i in range(30)],
    }
    row = parse_depth_snapshot(
        raw,
        symbol="BTCUSDT",
        market_type="USDM_FUTURES",
        captured_at=CAPTURED_AT,
        depth_levels=20,
    )
    # 27.3：只存 top20，原始深度不长期保留
    assert len(row["bids"]) == 20
    assert len(row["asks"]) == 20


def test_parse_depth_snapshot_rejects_crossed_book() -> None:
    raw = {"bids": [["42001.00", "1"]], "asks": [["42000.00", "1"]]}
    with pytest.raises(ValueError, match="交叉"):
        parse_depth_snapshot(
            raw, symbol="BTCUSDT", market_type="USDM_FUTURES", captured_at=CAPTURED_AT
        )


def test_parse_depth_snapshot_rejects_empty_side() -> None:
    with pytest.raises(ValueError, match="缺少买卖盘"):
        parse_depth_snapshot(
            {"bids": [], "asks": [["1", "1"]]},
            symbol="BTCUSDT",
            market_type="USDM_FUTURES",
            captured_at=CAPTURED_AT,
        )


def test_spread_bps_computed_from_mid() -> None:
    # (42000.10 - 42000.00) / 42000.05 * 10000 = 0.023809495464886351...
    value = spread_bps(
        best_bid=Decimal("42000.00"), best_ask=Decimal("42000.10")
    )
    assert float(value) == pytest.approx(0.023809495464886351, rel=1e-12)


# --------------------------------------------------------------------------
# 合约规格
# --------------------------------------------------------------------------


def test_parse_instrument_metadata_extracts_filters() -> None:
    raw = {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "contractType": "PERPETUAL",
        "quoteAsset": "USDT",
        "marginAsset": "USDT",
        "onboardDate": 1569398400000,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ],
    }
    row = parse_instrument_metadata(raw, market_type="USDM_FUTURES")
    assert row["tick_size"] == Decimal("0.10")
    assert row["price_precision"] == 1
    assert row["step_size"] == Decimal("0.001")
    assert row["quantity_precision"] == 3
    assert row["min_notional"] == Decimal("5")
    assert row["quote_asset"] == "USDT"
    assert row["onboard_date"] == dt.datetime(2019, 9, 25, 8, 0, tzinfo=UTC)


def test_parse_instrument_metadata_list_filters_non_trading() -> None:
    raw = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "contractType": "PERPETUAL",
                "quoteAsset": "USDT",
                "filters": [],
            },
            {
                "symbol": "OLDUSDT",
                "status": "BREAK",
                "contractType": "PERPETUAL",
                "quoteAsset": "USDT",
                "filters": [],
            },
            {
                "symbol": "BTCUSDT_240927",
                "status": "TRADING",
                "contractType": "CURRENT_QUARTER",
                "quoteAsset": "USDT",
                "filters": [],
            },
        ]
    }
    rows = parse_instrument_metadata_list(raw, market_type="USDM_FUTURES")
    assert [r["symbol"] for r in rows] == ["BTCUSDT"]
