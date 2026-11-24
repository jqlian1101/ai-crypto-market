"""衍生品与流动性特征的纯函数测试（PRD 4.5 / 22.2.4 / 29.6）。

**这些测试固定的是「不能搞反、不能用 0 替代」的不变量**：
基差两侧有效性、主动买卖方向、缺失时的 ``None`` 语义。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from aicrypto.domain.market.derivatives import (
    BASIS_MAX_TIME_SKEW_SECONDS,
    DataState,
    FundingPoint,
    OpenInterestPoint,
    OrderbookPoint,
    QuotePoint,
    TradePoint,
    compute_basis,
    depth_imbalance,
    funding_change,
    funding_flip,
    funding_percentile,
    large_trades,
    long_short_skew,
    oi_change_pct,
    oi_trend,
    price_oi_divergence,
    spread_bps,
    taker_volume_split,
)

UTC = dt.UTC
BASE = dt.datetime(2026, 1, 1, tzinfo=UTC)


def oi(hours_ago: float, value: str) -> OpenInterestPoint:
    return OpenInterestPoint(
        ts=BASE - dt.timedelta(hours=hours_ago), open_interest=Decimal(value)
    )


def fund(hours_ago: float, rate: str) -> FundingPoint:
    return FundingPoint(
        funding_time=BASE - dt.timedelta(hours=hours_ago),
        funding_rate=Decimal(rate),
        mark_price=Decimal("42000"),
    )


# --------------------------------------------------------------------------
# Open Interest
# --------------------------------------------------------------------------


def test_oi_change_pct_computes_window_change() -> None:
    points = [oi(2, "100"), oi(1, "110"), oi(0, "120")]
    # 1 小时窗口：取 <= 1h 前的最后一个点（110）到当前（120）
    assert oi_change_pct(points, window_seconds=3600) == pytest.approx(0.0909090909)


def test_oi_change_pct_returns_none_when_insufficient_samples() -> None:
    # 样本不足时返回 None 而非 0——0 会被误读为「持仓量没变」
    assert oi_change_pct([oi(0, "100")], window_seconds=3600) is None


def test_oi_change_pct_returns_none_when_window_uncovered() -> None:
    points = [oi(0.1, "100"), oi(0, "101")]
    # 窗口内没有起点采样，无法计算变化率
    assert oi_change_pct(points, window_seconds=3600) is None


def test_oi_trend_labels() -> None:
    up = [oi(2, "100"), oi(0, "110")]
    down = [oi(2, "110"), oi(0, "100")]
    flat = [oi(2, "100"), oi(0, "100")]
    assert oi_trend(up, window_seconds=3600) == "UP"
    assert oi_trend(down, window_seconds=3600) == "DOWN"
    assert oi_trend(flat, window_seconds=3600) == "FLAT"
    assert oi_trend([], window_seconds=3600) == "UNKNOWN"


# --------------------------------------------------------------------------
# Funding
# --------------------------------------------------------------------------


def test_funding_percentile_ranks_current_value() -> None:
    points = [fund(i, r) for i, r in enumerate(["0.001", "0.002", "0.003", "0.004"])]
    assert funding_percentile(points, current=Decimal("0.005")) == 1.0
    assert funding_percentile(points, current=Decimal("0.0005")) == 0.0


def test_funding_percentile_returns_none_when_insufficient_history() -> None:
    assert funding_percentile([fund(0, "0.001")], current=Decimal("0.001")) is None


def test_funding_change_is_difference_over_window() -> None:
    points = [fund(2, "0.001"), fund(1, "0.002"), fund(0, "0.003")]
    assert funding_change(points, lookback=2) == pytest.approx(0.002)


def test_funding_flip_detects_sign_change() -> None:
    positive = [fund(1, "0.001"), fund(0, "0.002")]
    crossed = [fund(1, "0.001"), fund(0, "-0.001")]
    assert funding_flip(positive) is False
    assert funding_flip(crossed) is True


# --------------------------------------------------------------------------
# 基差（29.6 的核心不变量）
# --------------------------------------------------------------------------


def quote(
    market_type: str,
    *,
    bid: str = "42000.00",
    ask: str = "42000.10",
    seconds_ago: float = 0.0,
    state: DataState = DataState.GOOD,
) -> QuotePoint:
    return QuotePoint(
        market_type=market_type,
        captured_at=BASE - dt.timedelta(seconds=seconds_ago),
        best_bid=Decimal(bid),
        best_ask=Decimal(ask),
        state=state,
    )


def test_basis_computed_when_both_sides_valid() -> None:
    spot = quote("SPOT")
    futures = quote("USDM_FUTURES", bid="42042.00", ask="42042.10")
    result = compute_basis(spot, futures)
    assert result.state is DataState.GOOD
    assert result.is_available
    assert result.basis_pct == pytest.approx(0.001, rel=1e-3)


def test_basis_missing_when_one_side_absent() -> None:
    result = compute_basis(quote("SPOT"), None)
    assert result.state is DataState.MISSING
    assert result.basis_pct is None
    assert not result.is_available


def test_basis_stale_when_one_side_stale() -> None:
    """29.6：任一方 STALE 则基差不计算，不用陈旧值替代。"""
    result = compute_basis(
        quote("SPOT", state=DataState.STALE), quote("USDM_FUTURES")
    )
    assert result.state is DataState.STALE
    assert result.basis_pct is None


def test_basis_stale_when_snapshots_out_of_sync() -> None:
    result = compute_basis(
        quote("SPOT", seconds_ago=0),
        quote("USDM_FUTURES", seconds_ago=BASIS_MAX_TIME_SKEW_SECONDS + 1),
    )
    assert result.state is DataState.STALE
    assert result.basis_pct is None
    assert "错位" in result.detail


def test_basis_missing_when_spot_mid_non_positive() -> None:
    result = compute_basis(
        quote("SPOT", bid="0", ask="0"), quote("USDM_FUTURES")
    )
    assert result.state is DataState.MISSING
    assert result.basis_pct is None


# --------------------------------------------------------------------------
# 流动性
# --------------------------------------------------------------------------


def book(
    *,
    bid: str = "42000.00",
    ask: str = "42000.10",
    bid_qty: str = "3.0",
    ask_qty: str = "1.0",
) -> OrderbookPoint:
    return OrderbookPoint(
        captured_at=BASE,
        best_bid=Decimal(bid),
        best_ask=Decimal(ask),
        bid_qty_sum=Decimal(bid_qty),
        ask_qty_sum=Decimal(ask_qty),
    )


def test_depth_imbalance_sign_and_bounds() -> None:
    assert depth_imbalance(book()) == pytest.approx(0.5)
    assert depth_imbalance(book(bid_qty="1.0", ask_qty="3.0")) == pytest.approx(-0.5)
    assert depth_imbalance(book(bid_qty="1.0", ask_qty="1.0")) == 0.0


def test_depth_imbalance_zero_when_no_depth() -> None:
    assert depth_imbalance(book(bid_qty="0", ask_qty="0")) == 0.0


def test_spread_bps_uses_mid_price() -> None:
    assert spread_bps(book()) == pytest.approx(0.023809495464886348, rel=1e-12)


def test_taker_volume_split_counts_direction() -> None:
    trades = [
        TradePoint(BASE, Decimal("42000"), Decimal("2"), True),
        TradePoint(BASE, Decimal("42000"), Decimal("1"), False),
        TradePoint(BASE, Decimal("42000"), Decimal("1"), True),
    ]
    split = taker_volume_split(trades)
    assert split["taker_buy_volume"] == 3.0
    assert split["taker_sell_volume"] == 1.0
    assert split["net_volume"] == 2.0
    assert split["buy_ratio"] == 0.75


def test_taker_volume_split_handles_no_trades() -> None:
    split = taker_volume_split([])
    assert split["buy_ratio"] == 0.0
    assert split["net_volume"] == 0.0


def test_large_trades_requires_minimum_sample() -> None:
    trades = [
        TradePoint(BASE, Decimal("42000"), Decimal("1"), True) for _ in range(10)
    ]
    # 样本太少时高分位没有统计意义
    assert large_trades(trades) == []


def test_large_trades_flags_top_quantile() -> None:
    trades = [
        TradePoint(BASE, Decimal("42000"), Decimal("1"), True) for _ in range(30)
    ]
    trades.append(TradePoint(BASE, Decimal("42000"), Decimal("100"), True))
    found = large_trades(trades)
    assert len(found) == 1
    assert found[0]["quantity"] == "100"
    assert found[0]["notional"] == "4200000"


# --------------------------------------------------------------------------
# 价格与 OI 联合
# --------------------------------------------------------------------------


def test_price_oi_divergence_detects_opposite_moves() -> None:
    # 价格涨、持仓跌 -> 疑似平仓推动
    assert price_oi_divergence(0.02, -0.05) is True
    # 价格跌、持仓涨 -> 疑似新空进场
    assert price_oi_divergence(-0.02, 0.05) is True


def test_price_oi_divergence_false_when_aligned() -> None:
    assert price_oi_divergence(0.02, 0.05) is False
    assert price_oi_divergence(-0.02, -0.05) is False


def test_price_oi_divergence_returns_false_when_missing() -> None:
    assert price_oi_divergence(None, 0.05) is False
    assert price_oi_divergence(0.02, None) is False


def test_long_short_skew_labels() -> None:
    assert long_short_skew(oi_change=0.05, funding_rate=0.001, price_change_pct=None) == (
        "LONG_CROWDED"
    )
    assert long_short_skew(oi_change=0.05, funding_rate=-0.001, price_change_pct=None) == (
        "SHORT_CROWDED"
    )
    assert long_short_skew(oi_change=None, funding_rate=None, price_change_pct=None) == (
        "UNKNOWN"
    )
