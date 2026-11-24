"""衍生品与流动性特征计算（PRD 4.5 / 22.2.4 / 29.6）。

**不变量**：

1. **基差只在两侧都有效时计算**（29.6）——现货或期货任一方 ``STALE``/``MISSING``
   时基差不计算，标记 ``missing_data``，**不得用陈旧值或 0 替代**。
2. **基差用同一时刻的中间价**，不用收盘价（收盘价不同步会引入伪基差）。
3. **不得把有限盘口快照推断为确定的机构意图**（4.5）——深度不平衡只描述盘口结构，
   不做多空意图判断。
4. **百分比一律存小数**（30.1）。

本模块只做纯计算，不做 IO；数据读取与快照组装在服务层完成。
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

#: 基差计算允许的最大时间错位（秒）。两侧快照时间差超过它就不算基差。
BASIS_MAX_TIME_SKEW_SECONDS = 30.0


class DataState(StrEnum):
    """数据可用状态。基差等跨市场特征依赖它。"""

    GOOD = "GOOD"
    STALE = "STALE"
    MISSING = "MISSING"


@dataclass(frozen=True, slots=True)
class OpenInterestPoint:
    """一条持仓量采样点。"""

    ts: dt.datetime
    open_interest: Decimal


@dataclass(frozen=True, slots=True)
class FundingPoint:
    """一条资金费率记录。"""

    funding_time: dt.datetime
    funding_rate: Decimal
    mark_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class OrderbookPoint:
    """一条盘口快照（只含 top20）。"""

    captured_at: dt.datetime
    best_bid: Decimal
    best_ask: Decimal
    bid_qty_sum: Decimal
    ask_qty_sum: Decimal

    @property
    def mid_price(self) -> Decimal:
        return (self.best_bid + self.best_ask) / Decimal("2")


@dataclass(frozen=True, slots=True)
class TradePoint:
    """一条聚合成交。"""

    exchange_ts: dt.datetime
    price: Decimal
    quantity: Decimal
    is_taker_buy: bool


@dataclass(frozen=True, slots=True)
class QuotePoint:
    """一个市场的报价快照（现货或期货）。"""

    market_type: str
    captured_at: dt.datetime
    best_bid: Decimal
    best_ask: Decimal
    state: DataState = DataState.GOOD

    @property
    def mid_price(self) -> Decimal:
        return (self.best_bid + self.best_ask) / Decimal("2")


# --------------------------------------------------------------------------
# Open Interest
# --------------------------------------------------------------------------


def oi_change_pct(
    points: Sequence[OpenInterestPoint], *, window_seconds: int
) -> float | None:
    """窗口内持仓量变化率（小数）。

    取窗口起点与终点两个采样点。样本不足（少于 2 个点）时返回 ``None``，
    **不返回 0**——0 会被误读为「持仓量没变」。
    """
    if len(points) < 2 or window_seconds <= 0:
        return None
    ordered = sorted(points, key=lambda p: p.ts)
    end = ordered[-1]
    cutoff = end.ts - dt.timedelta(seconds=window_seconds)
    earlier = [p for p in ordered if p.ts <= cutoff]
    if not earlier:
        return None
    start = earlier[-1]
    if start.open_interest <= 0:
        return None
    return float((end.open_interest - start.open_interest) / start.open_interest)


def oi_trend(
    points: Sequence[OpenInterestPoint], *, window_seconds: int
) -> str:
    """持仓量趋势标签：``UP`` / ``DOWN`` / ``FLAT`` / ``UNKNOWN``。"""
    change = oi_change_pct(points, window_seconds=window_seconds)
    if change is None:
        return "UNKNOWN"
    if change > 0.005:
        return "UP"
    if change < -0.005:
        return "DOWN"
    return "FLAT"


# --------------------------------------------------------------------------
# Funding
# --------------------------------------------------------------------------


def funding_percentile(
    points: Sequence[FundingPoint], *, current: Decimal, lookback: int = 100
) -> float | None:
    """当前资金费率在历史样本中的分位（0–1）。

    历史样本不足时返回 ``None``（不返回 0，避免「处于最低分位」的误判）。
    """
    rates = [p.funding_rate for p in points[-lookback:]]
    if len(rates) < 2:
        return None
    below = sum(1 for rate in rates if rate < current)
    equal = sum(1 for rate in rates if rate == current)
    return (below + equal / 2) / len(rates)


def funding_change(
    points: Sequence[FundingPoint], *, lookback: int = 3
) -> float | None:
    """资金费率变化量（当前值 − ``lookback`` 期前的值）。"""
    if len(points) < 2:
        return None
    ordered = sorted(points, key=lambda p: p.funding_time)
    window = ordered[-(lookback + 1) :]
    if len(window) < 2:
        return None
    return float(window[-1].funding_rate - window[0].funding_rate)


def funding_flip(
    points: Sequence[FundingPoint], *, lookback: int = 2
) -> bool:
    """资金费率是否在最近 ``lookback`` 期内穿越零点（``DER_FUNDING_FLIP``）。"""
    if len(points) < 2:
        return False
    ordered = sorted(points, key=lambda p: p.funding_time)
    window = ordered[-(lookback + 1) :]
    signs = [1 if p.funding_rate > 0 else -1 if p.funding_rate < 0 else 0 for p in window]
    return any(a != 0 and b != 0 and a != b for a, b in itertools.pairwise(signs))


# --------------------------------------------------------------------------
# 基差
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BasisResult:
    """基差计算结果。``basis_pct`` 为 ``None`` 时必须看 ``state``。"""

    basis_pct: float | None
    state: DataState
    detail: str = ""

    @property
    def is_available(self) -> bool:
        return self.basis_pct is not None


def compute_basis(
    spot: QuotePoint | None, futures: QuotePoint | None
) -> BasisResult:
    """计算 Spot-Futures 基差（小数，如 0.0012 表示 0.12%）。

    规则（29.6）：

    - 任一方缺失 → ``MISSING``；
    - 任一方 ``STALE`` → ``STALE``；
    - 两侧快照时间错位超过 :data:`BASIS_MAX_TIME_SKEW_SECONDS` → ``STALE``；
    - 现货中间价 ≤ 0 → ``MISSING``（不用 0 替代）；
    - 正常情况返回 ``GOOD`` 与基差值。
    """
    if spot is None or futures is None:
        return BasisResult(None, DataState.MISSING, "缺少现货或期货报价")
    if spot.state is not DataState.GOOD or futures.state is not DataState.GOOD:
        stale_side = []
        if spot.state is not DataState.GOOD:
            stale_side.append("现货")
        if futures.state is not DataState.GOOD:
            stale_side.append("期货")
        return BasisResult(
            None, DataState.STALE, f"{'、'.join(stale_side)}报价陈旧，不计算基差"
        )
    skew = abs((futures.captured_at - spot.captured_at).total_seconds())
    if skew > BASIS_MAX_TIME_SKEW_SECONDS:
        return BasisResult(
            None, DataState.STALE, f"两侧快照时间错位 {skew:.1f}s 超过阈值"
        )
    mid = spot.mid_price
    if mid <= 0:
        return BasisResult(None, DataState.MISSING, "现货中间价非正")
    basis = float((futures.mid_price - mid) / mid)
    return BasisResult(basis, DataState.GOOD)


# --------------------------------------------------------------------------
# 流动性
# --------------------------------------------------------------------------


def spread_bps(book: OrderbookPoint) -> float:
    """点差（基点）。"""
    mid = book.mid_price
    if mid <= 0:
        return 0.0
    return float((book.best_ask - book.best_bid) / mid * Decimal("10000"))


def depth_imbalance(book: OrderbookPoint) -> float:
    """买卖盘深度不平衡，取值 ``[-1, 1]``。

    正值表示买盘深度占优。**只描述盘口结构，不代表多空意图**（4.5）。
    """
    total = book.bid_qty_sum + book.ask_qty_sum
    if total <= 0:
        return 0.0
    return float((book.bid_qty_sum - book.ask_qty_sum) / total)


def taker_volume_split(trades: Sequence[TradePoint]) -> dict[str, float]:
    """主动买卖量与净额。

    返回 ``taker_buy_volume`` / ``taker_sell_volume`` / ``net_volume`` /
    ``buy_ratio``。``buy_ratio`` 是主动买入占总量的比例。
    """
    buy = sum((t.quantity for t in trades if t.is_taker_buy), Decimal("0"))
    sell = sum((t.quantity for t in trades if not t.is_taker_buy), Decimal("0"))
    total = buy + sell
    return {
        "taker_buy_volume": float(buy),
        "taker_sell_volume": float(sell),
        "net_volume": float(buy - sell),
        "buy_ratio": float(buy / total) if total > 0 else 0.0,
    }


def large_trades(
    trades: Sequence[TradePoint], *, quantile: float = 0.99
) -> list[dict[str, Any]]:
    """大额成交：成交量超过给定分位阈值的成交。

    分位按同批成交的成交量计算。样本不足 20 笔时返回空列表
    （样本太少时高分位没有统计意义）。
    """
    if len(trades) < 20:
        return []
    quantities = sorted(t.quantity for t in trades)
    index = min(len(quantities) - 1, max(0, int(len(quantities) * quantile)))
    threshold = quantities[index]
    if threshold <= 0:
        return []
    return [
        {
            "exchange_ts": t.exchange_ts.isoformat(),
            "price": str(t.price),
            "quantity": str(t.quantity),
            "is_taker_buy": t.is_taker_buy,
            "notional": str(t.price * t.quantity),
        }
        for t in trades
        if t.quantity >= threshold
    ]


def price_oi_divergence(
    price_change_pct: float | None, oi_change: float | None, *, threshold: float = 0.01
) -> bool:
    """价格与持仓量是否反向变动超阈值（``DER_PRICE_OI_DIVERGENCE``）。

    价格上涨但持仓量下降 → 疑似平仓推动；价格下跌但持仓量上升 → 疑似新空进场。
    两者缺失时返回 ``False``（不猜测）。
    """
    if price_change_pct is None or oi_change is None:
        return False
    if price_change_pct > threshold and oi_change < -threshold:
        return True
    return bool(price_change_pct < -threshold and oi_change > threshold)


def long_short_skew(
    *,
    oi_change: float | None,
    funding_rate: float | None,
    price_change_pct: float | None,
) -> str:
    """多空拥挤迹象的粗分类。

    只输出**倾向性标签**（``LONG_CROWDED`` / ``SHORT_CROWDED`` / ``NEUTRAL`` /
    ``UNKNOWN``），不输出确定性结论——多空比本身不可直接观测，这里用
    「资金费率 + 持仓量 + 价格」的联合方向做保守推断。
    """
    if funding_rate is None:
        return "UNKNOWN"
    if funding_rate > 0 and oi_change is not None and oi_change > 0.01:
        return "LONG_CROWDED"
    if funding_rate < 0 and oi_change is not None and oi_change > 0.01:
        return "SHORT_CROWDED"
    if price_change_pct is not None and funding_rate > 0 and price_change_pct > 0.02:
        return "LONG_CROWDED"
    if price_change_pct is not None and funding_rate < 0 and price_change_pct < -0.02:
        return "SHORT_CROWDED"
    return "NEUTRAL"


__all__ = [
    "BASIS_MAX_TIME_SKEW_SECONDS",
    "BasisResult",
    "DataState",
    "FundingPoint",
    "OpenInterestPoint",
    "OrderbookPoint",
    "QuotePoint",
    "TradePoint",
    "compute_basis",
    "depth_imbalance",
    "funding_change",
    "funding_flip",
    "funding_percentile",
    "large_trades",
    "long_short_skew",
    "oi_change_pct",
    "oi_trend",
    "price_oi_divergence",
    "spread_bps",
    "taker_volume_split",
]
