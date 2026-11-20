"""纯 K 线市场结构与市场状态（PRD 4.4 / 4.6，仅 K 线部分）。

不依赖衍生品与盘口数据；这两类字段在快照中由调用方显式标注缺失。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from aicrypto.domain.market.indicators import atr, ema_series


@dataclass(frozen=True, slots=True)
class SwingPoint:
    index: int
    kind: str  # "HIGH" | "LOW"
    price: float


def swing_points(
    highs: Sequence[Decimal], lows: Sequence[Decimal], *, left: int = 2, right: int = 2
) -> list[SwingPoint]:
    """摆动高低点：左右各 ``left`` / ``right`` 根内为极值。

    末尾 ``right`` 根无法确认，不纳入结果，避免使用未来 K 线（防泄漏）。
    """
    if len(highs) != len(lows):
        raise ValueError("highs/lows 长度必须一致")
    h = [float(v) for v in highs]
    lo = [float(v) for v in lows]
    points: list[SwingPoint] = []
    for i in range(left, len(h) - right):
        window_h = h[i - left : i + right + 1]
        window_l = lo[i - left : i + right + 1]
        if h[i] == max(window_h) and window_h.count(h[i]) == 1:
            points.append(SwingPoint(index=i, kind="HIGH", price=h[i]))
        if lo[i] == min(window_l) and window_l.count(lo[i]) == 1:
            points.append(SwingPoint(index=i, kind="LOW", price=lo[i]))
    return points


class StructureBias(StrEnum):
    UPTREND = "UPTREND"  # 高点与低点均抬高
    DOWNTREND = "DOWNTREND"  # 高点与低点均降低
    RANGE = "RANGE"  # 混合或不足
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class StructureSummary:
    bias: StructureBias
    last_swing_high: float | None
    last_swing_low: float | None
    higher_highs: bool | None
    higher_lows: bool | None
    swing_count: int


def summarize_structure(
    highs: Sequence[Decimal], lows: Sequence[Decimal], *, left: int = 2, right: int = 2
) -> StructureSummary:
    """依据最近两个摆动高点与低点判断 HH/HL 序列。"""
    points = swing_points(highs, lows, left=left, right=right)
    swing_highs = [p for p in points if p.kind == "HIGH"]
    swing_lows = [p for p in points if p.kind == "LOW"]
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return StructureSummary(
            bias=StructureBias.INSUFFICIENT,
            last_swing_high=swing_highs[-1].price if swing_highs else None,
            last_swing_low=swing_lows[-1].price if swing_lows else None,
            higher_highs=None,
            higher_lows=None,
            swing_count=len(points),
        )
    hh = swing_highs[-1].price > swing_highs[-2].price
    hl = swing_lows[-1].price > swing_lows[-2].price
    lh = swing_highs[-1].price < swing_highs[-2].price
    ll = swing_lows[-1].price < swing_lows[-2].price
    if hh and hl:
        bias = StructureBias.UPTREND
    elif lh and ll:
        bias = StructureBias.DOWNTREND
    else:
        bias = StructureBias.RANGE
    return StructureSummary(
        bias=bias,
        last_swing_high=swing_highs[-1].price,
        last_swing_low=swing_lows[-1].price,
        higher_highs=hh,
        higher_lows=hl,
        swing_count=len(points),
    )


@dataclass(frozen=True, slots=True)
class SupportResistanceZone:
    low: float
    high: float
    touches: int
    kind: str  # "SUPPORT" | "RESISTANCE"


def sr_zones(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    *,
    left: int = 2,
    right: int = 2,
    tolerance_ratio: float = 0.003,
    max_zones: int = 6,
) -> list[SupportResistanceZone]:
    """把摆动点按价格相近度聚类为支撑/阻力区间。

    ``tolerance_ratio``：相邻摆动价差在该比例内视为同一区间。
    区间类型以最新收盘价为界：低于收盘价为支撑，高于为阻力。
    """
    points = swing_points(highs, lows, left=left, right=right)
    if not points or not closes:
        return []
    last_close = float(closes[-1])
    prices = sorted(p.price for p in points)
    clusters: list[list[float]] = [[prices[0]]]
    for price in prices[1:]:
        base = clusters[-1][0]
        if base > 0 and (price - base) / base <= tolerance_ratio:
            clusters[-1].append(price)
        else:
            clusters.append([price])
    zones: list[SupportResistanceZone] = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        low, high = min(cluster), max(cluster)
        zones.append(
            SupportResistanceZone(
                low=low,
                high=high,
                touches=len(cluster),
                kind="SUPPORT" if high < last_close else "RESISTANCE",
            )
        )
    zones.sort(key=lambda z: z.touches, reverse=True)
    return zones[:max_zones]


class MarketRegime(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class RegimeSummary:
    regime: MarketRegime
    ema_fast: float | None
    ema_slow: float | None
    atr_ratio: float | None  # ATR / 收盘价
    atr_percentile: float | None  # 当前 ATR 比例在历史窗口中的分位


def classify_regime(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    *,
    fast: int = 20,
    slow: int = 50,
    atr_period: int = 14,
    history: int = 100,
    high_vol_percentile: float = 0.90,
    high_vol_multiple: float = 1.5,
    trend_slope_threshold: float = 0.0,
) -> RegimeSummary:
    """基于 EMA 结构与 ATR 百分位的 K 线版市场状态。

    ATR 按收盘价归一化后，持续下跌会使比例自然抬升，单看分位会把趋势误判为高波动，
    因此高波动需同时满足：

    - 当前 ATR 比例分位 >= ``high_vol_percentile``；
    - 当前 ATR 比例 >= 窗口中位数 × ``high_vol_multiple``。

    规则（按优先级）：

    1. 样本不足 ``slow + history`` 时返回 ``INSUFFICIENT``。
    2. 同时满足上述两条 → ``HIGH_VOLATILITY``。
    3. EMA_fast > EMA_slow 且快线斜率 > 阈值 → ``TREND_UP``；反向 → ``TREND_DOWN``。
    4. 其余 → ``RANGE``。
    """
    closes_f = [float(c) for c in closes]
    fast_series = ema_series(closes_f, fast)
    slow_series = ema_series(closes_f, slow)
    ema_fast_last = fast_series[-1] if fast_series else None
    ema_slow_last = slow_series[-1] if slow_series else None

    atr_ratios: list[float] = []
    for end in range(atr_period + 1, len(closes) + 1):
        value = atr(highs[:end], lows[:end], closes[:end], atr_period)
        if value is None or closes_f[end - 1] <= 0:
            continue
        atr_ratios.append(value / closes_f[end - 1])
    current_ratio = atr_ratios[-1] if atr_ratios else None

    if len(closes) < slow + history or ema_fast_last is None or ema_slow_last is None:
        return RegimeSummary(
            regime=MarketRegime.INSUFFICIENT,
            ema_fast=ema_fast_last,
            ema_slow=ema_slow_last,
            atr_ratio=current_ratio,
            atr_percentile=None,
        )

    window = atr_ratios[-history:]
    percentile = (
        sum(1 for x in window if x <= current_ratio) / len(window)
        if current_ratio is not None and window
        else None
    )
    median_ratio = sorted(window)[len(window) // 2] if window else None
    is_extreme = (
        percentile is not None
        and current_ratio is not None
        and median_ratio is not None
        and percentile >= high_vol_percentile
        and current_ratio >= median_ratio * high_vol_multiple
    )

    if is_extreme:
        regime = MarketRegime.HIGH_VOLATILITY
    else:
        prev_fast = fast_series[-2] if len(fast_series) >= 2 else None
        slope = (ema_fast_last - prev_fast) if prev_fast is not None else 0.0
        if ema_fast_last > ema_slow_last and slope > trend_slope_threshold:
            regime = MarketRegime.TREND_UP
        elif ema_fast_last < ema_slow_last and slope < -trend_slope_threshold:
            regime = MarketRegime.TREND_DOWN
        else:
            regime = MarketRegime.RANGE

    return RegimeSummary(
        regime=regime,
        ema_fast=ema_fast_last,
        ema_slow=ema_slow_last,
        atr_ratio=current_ratio,
        atr_percentile=percentile,
    )


__all__ = [
    "MarketRegime",
    "RegimeSummary",
    "StructureBias",
    "StructureSummary",
    "SupportResistanceZone",
    "SwingPoint",
    "classify_regime",
    "sr_zones",
    "summarize_structure",
    "swing_points",
]
