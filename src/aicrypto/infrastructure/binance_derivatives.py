"""Binance 衍生品与盘口 REST 端点的常量与纯函数（PRD 27.2 / 29.2 / 29.7 / 30.2）。

本模块**不做任何 IO**：端点表、权重表与响应解析都是纯函数，可直接单元测试。
实际请求在 :mod:`aicrypto.infrastructure.binance_derivatives_client` 中完成。

**为什么 OI 与 Funding 不走 WS**（29.2）：Binance 没有 Open Interest 的 WS 推送，
只能 REST 轮询，因此 ``DER_OI_SURGE`` 的检出延迟天然高于价格异动（29.4.3）。
Funding 虽有 ``markPrice`` 流携带当前费率，但该费率是「下一期」的预估，
``funding_history`` 仍以 REST 为权威。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from aicrypto.infrastructure.binance_rest import EXCHANGE, dec, ms_to_dt

# --------------------------------------------------------------------------
# 端点与权重（27.2）
# --------------------------------------------------------------------------

FUTURES_BASE_URL = "https://fapi.binance.com"
SPOT_BASE_URL = "https://api.binance.com"

#: market_type -> REST 基础地址
BASE_URLS: dict[str, str] = {
    "SPOT": SPOT_BASE_URL,
    "USDM_FUTURES": FUTURES_BASE_URL,
}

#: market_type -> (路径, 权重)
OI_PATH = "/fapi/v1/openInterest"
OI_HISTORY_PATH = "/futures/data/openInterestHist"
FUNDING_RATE_PATH = "/fapi/v1/fundingRate"
PREMIUM_INDEX_PATH = "/fapi/v1/premiumIndex"
FUTURES_DEPTH_PATH = "/fapi/v1/depth"
SPOT_DEPTH_PATH = "/api/v3/depth"
FUTURES_EXCHANGE_INFO_PATH = "/fapi/v1/exchangeInfo"
SPOT_EXCHANGE_INFO_PATH = "/api/v3/exchangeInfo"

#: 单页上限
FUNDING_PAGE_LIMIT = 1000
OI_HISTORY_PAGE_LIMIT = 500
DEPTH_MAX_LIMIT = 100


def depth_weight(market_type: str, limit: int) -> int:
    """深度接口权重（Binance 文档）。

    - USDⓈ-M：limit ≤ 100 → 2；500 → 5；1000 → 10
    - 现货：limit ≤ 100 → 2；500 → 10；1000 → 20
    """
    if limit <= 100:
        return 2
    if limit <= 500:
        return 5 if market_type == "USDM_FUTURES" else 10
    return 10 if market_type == "USDM_FUTURES" else 20


def exchange_info_weight(market_type: str) -> int:
    """合约规格接口权重。"""
    return 1 if market_type == "USDM_FUTURES" else 20


#: OI / Funding / premiumIndex / OI 历史 的固定权重（均为 1）
LOW_WEIGHT_ENDPOINTS = frozenset(
    {
        "open_interest",
        "open_interest_history",
        "funding_rate",
        "premium_index",
    }
)


def endpoint_weight(endpoint: str, *, market_type: str, limit: int | None = None) -> int:
    """按接口名返回权重，供统一限流器扣减（27.2）。"""
    if endpoint in LOW_WEIGHT_ENDPOINTS:
        return 1
    if endpoint == "depth":
        return depth_weight(market_type, limit or DEPTH_MAX_LIMIT)
    if endpoint == "exchange_info":
        return exchange_info_weight(market_type)
    raise ValueError(f"未知接口: {endpoint!r}")


# --------------------------------------------------------------------------
# 解析：Open Interest
# --------------------------------------------------------------------------


def parse_open_interest(raw: dict[str, Any], *, market_type: str = "USDM_FUTURES") -> dict[str, Any]:
    """解析 ``/fapi/v1/openInterest`` 的当前持仓量。

    响应形如 ``{"openInterest": "12345.678", "symbol": "BTCUSDT", "time": 1700000000000}``。
    """
    symbol = str(raw["symbol"]).upper()
    open_interest = dec(str(raw["openInterest"]))
    ts = ms_to_dt(int(raw["time"]))
    return {
        "exchange": EXCHANGE,
        "market_type": market_type,
        "symbol": symbol,
        "ts": ts,
        "open_interest": open_interest,
        # 当前接口不返回计价币价值，留空而不是填 0（PRD 4.3：不得用 0 替代缺失）
        "open_interest_value": None,
    }


def parse_open_interest_history(
    raw: list[dict[str, Any]], *, market_type: str = "USDM_FUTURES"
) -> list[dict[str, Any]]:
    """解析 ``/futures/data/openInterestHist`` 的历史持仓量。

    响应项形如::

        {"symbol": "BTCUSDT", "sumOpenInterest": "12345.678",
         "sumOpenInterestValue": "987654321.0", "timestamp": 1700000000000}
    """
    rows: list[dict[str, Any]] = []
    for item in raw:
        symbol = str(item["symbol"]).upper()
        value = item.get("sumOpenInterestValue")
        rows.append(
            {
                "exchange": EXCHANGE,
                "market_type": market_type,
                "symbol": symbol,
                "ts": ms_to_dt(int(item["timestamp"])),
                "open_interest": dec(str(item["sumOpenInterest"])),
                "open_interest_value": None if value is None else dec(str(value)),
            }
        )
    rows.sort(key=lambda row: row["ts"])
    return rows


# --------------------------------------------------------------------------
# 解析：Funding
# --------------------------------------------------------------------------


def parse_funding_rate(
    raw: dict[str, Any], *, market_type: str = "USDM_FUTURES"
) -> dict[str, Any]:
    """解析 ``/fapi/v1/fundingRate`` 的一条资金费率记录。

    响应形如::

        {"symbol": "BTCUSDT", "fundingRate": "0.00010000",
         "fundingTime": 1700000000000, "markPrice": "42000.00"}
    """
    symbol = str(raw["symbol"]).upper()
    funding_time = ms_to_dt(int(raw["fundingTime"]))
    mark_price_raw = raw.get("markPrice")
    return {
        "exchange": EXCHANGE,
        "market_type": market_type,
        "symbol": symbol,
        "funding_time": funding_time,
        "funding_rate": dec(str(raw["fundingRate"])),
        # markPrice 缺失时填 None，由调用方决定回退策略（表中该列 NOT NULL）
        "mark_price": None if mark_price_raw is None else dec(str(mark_price_raw)),
    }


def parse_premium_index(
    raw: dict[str, Any], *, market_type: str = "USDM_FUTURES"
) -> dict[str, Any]:
    """解析 ``/fapi/v1/premiumIndex``：标记价格、指数价格与当前预估资金费率。

    这是 funding 记录缺少 ``markPrice`` 时的回退来源，同时也是
    ``DER_FUNDING_EXTREME`` 的低延迟触发源（29.2）。
    """
    symbol = str(raw["symbol"]).upper()
    next_funding = raw.get("nextFundingTime")
    return {
        "exchange": EXCHANGE,
        "market_type": market_type,
        "symbol": symbol,
        "mark_price": dec(str(raw["markPrice"])),
        "index_price": dec(str(raw["indexPrice"])) if raw.get("indexPrice") else None,
        "last_funding_rate": dec(str(raw["lastFundingRate"])),
        "next_funding_time": None if next_funding is None else ms_to_dt(int(next_funding)),
        "exchange_ts": ms_to_dt(int(raw["time"])) if raw.get("time") else None,
    }


# --------------------------------------------------------------------------
# 解析：盘口深度
# --------------------------------------------------------------------------


def parse_depth_snapshot(
    raw: dict[str, Any],
    *,
    symbol: str,
    market_type: str,
    captured_at: dt.datetime,
    depth_levels: int = 20,
) -> dict[str, Any]:
    """解析深度快照为 ``orderbook_snapshots`` 行。

    只保留 **top20**（27.3 / 29.7）：原始深度不长期保留，高频 WS 数据仅用于
    派生特征。价格与数量以**字符串**写入 JSONB，避免 JSON 浮点丢精度。

    响应形如::

        {"lastUpdateId": 1, "E": 1700000000000, "T": 1700000000000,
         "bids": [["42000.00", "1.5"], ...], "asks": [["42000.10", "0.9"], ...]}
    """
    bids_raw = raw.get("bids") or []
    asks_raw = raw.get("asks") or []
    if not bids_raw or not asks_raw:
        raise ValueError("深度快照缺少买卖盘")

    bids = [[str(price), str(qty)] for price, qty in bids_raw[:depth_levels]]
    asks = [[str(price), str(qty)] for price, qty in asks_raw[:depth_levels]]

    best_bid = dec(bids[0][0])
    best_ask = dec(asks[0][0])
    if best_ask <= best_bid:
        raise ValueError(f"盘口交叉：best_ask={best_ask} <= best_bid={best_bid}")

    bid_qty_sum = sum((dec(qty) for _, qty in bids), Decimal("0"))
    ask_qty_sum = sum((dec(qty) for _, qty in asks), Decimal("0"))

    exchange_ts = raw.get("E") or raw.get("T")
    return {
        "exchange": EXCHANGE,
        "market_type": market_type,
        "symbol": symbol.upper(),
        "captured_at": captured_at,
        "exchange_ts": None if exchange_ts is None else ms_to_dt(int(exchange_ts)),
        "bids": bids,
        "asks": asks,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_qty_sum": bid_qty_sum,
        "ask_qty_sum": ask_qty_sum,
        "spread_bps": spread_bps(best_bid=best_bid, best_ask=best_ask),
    }


def spread_bps(*, best_bid: Decimal, best_ask: Decimal) -> Decimal:
    """点差（基点）：``(ask - bid) / mid × 10000``。"""
    mid = (best_bid + best_ask) / Decimal("2")
    if mid <= 0:
        raise ValueError("中间价必须为正")
    return (best_ask - best_bid) / mid * Decimal("10000")


# --------------------------------------------------------------------------
# 解析：合约规格
# --------------------------------------------------------------------------


def parse_instrument_metadata(
    raw: dict[str, Any], *, market_type: str
) -> dict[str, Any]:
    """解析 ``exchangeInfo`` 中单个 symbol 的合约规格。

    过滤器取值：``PRICE_FILTER.tickSize``、``LOT_SIZE.stepSize/minQty``、
    ``MIN_NOTIONAL.notional``（合约用 ``MIN_NOTIONAL`` 或 ``NOTIONAL``）。
    """
    filters = {f.get("filterType"): f for f in raw.get("filters", [])}
    price_filter = filters.get("PRICE_FILTER", {})
    lot_size = filters.get("LOT_SIZE", {})
    notional = filters.get("MIN_NOTIONAL", filters.get("NOTIONAL", {}))

    tick_size = dec(str(price_filter.get("tickSize", "0")))
    step_size = dec(str(lot_size.get("stepSize", "0")))
    min_qty = dec(str(lot_size.get("minQty", "0")))
    min_notional = dec(str(notional.get("notional", "0")))

    quote_asset = str(raw.get("quoteAsset", "")).upper()
    margin_asset = raw.get("marginAsset")
    onboard_date = raw.get("onboardDate")

    return {
        "exchange": EXCHANGE,
        "market_type": market_type,
        "symbol": str(raw["symbol"]).upper(),
        "price_precision": _precision(tick_size),
        "quantity_precision": _precision(step_size),
        "tick_size": tick_size,
        "step_size": step_size,
        "min_qty": min_qty,
        "min_notional": min_notional,
        "contract_multiplier": Decimal("1"),
        "quote_asset": quote_asset,
        "margin_asset": None if margin_asset is None else str(margin_asset).upper(),
        "onboard_date": None if onboard_date is None else ms_to_dt(int(onboard_date)),
        "raw": raw,
    }


def _precision(step: Decimal) -> int:
    """由最小变动单位推出小数位数。"""
    if step <= 0:
        return 0
    exponent = step.normalize().as_tuple().exponent
    return max(0, -int(exponent))


def parse_instrument_metadata_list(
    raw: dict[str, Any], *, market_type: str
) -> list[dict[str, Any]]:
    """解析 ``exchangeInfo`` 全量响应，只保留交易中的永续合约。"""
    rows: list[dict[str, Any]] = []
    for item in raw.get("symbols", []):
        if item.get("status") != "TRADING":
            continue
        if item.get("contractType") not in (None, "PERPETUAL"):
            continue
        rows.append(parse_instrument_metadata(item, market_type=market_type))
    return rows


__all__ = [
    "BASE_URLS",
    "DEPTH_MAX_LIMIT",
    "FUNDING_PAGE_LIMIT",
    "FUNDING_RATE_PATH",
    "FUTURES_BASE_URL",
    "FUTURES_DEPTH_PATH",
    "FUTURES_EXCHANGE_INFO_PATH",
    "LOW_WEIGHT_ENDPOINTS",
    "OI_HISTORY_PAGE_LIMIT",
    "OI_HISTORY_PATH",
    "OI_PATH",
    "PREMIUM_INDEX_PATH",
    "SPOT_BASE_URL",
    "SPOT_DEPTH_PATH",
    "SPOT_EXCHANGE_INFO_PATH",
    "depth_weight",
    "endpoint_weight",
    "exchange_info_weight",
    "parse_depth_snapshot",
    "parse_funding_rate",
    "parse_instrument_metadata",
    "parse_instrument_metadata_list",
    "parse_open_interest",
    "parse_open_interest_history",
    "parse_premium_index",
    "spread_bps",
]
