"""纯 K 线技术指标（PRD 4.5 / 第 38 章特征单一实现源）。

约束：

- 纯函数：无 I/O、无 ``now()``、无全局状态（38.2.1）。
- 输入按 ``open_time`` 升序的已收盘 K 线序列，最后一根即为最新值。
- 样本不足时返回 ``None``，绝不用不完整窗口给出数值（预热约束）。
- 计算结果带 ``FEATURE_VERSION``，口径变更须升版本（38.2.2）。
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

#: 特征计算口径版本；指标公式、窗口、取值字段变更时必须升级
FEATURE_VERSION = "indicators-1.0.0"


def _floats(values: Sequence[Decimal]) -> list[float]:
    return [float(v) for v in values]


def sma(values: Sequence[Decimal], period: int) -> float | None:
    """简单移动平均，取最后 ``period`` 个值的均值。"""
    _check_period(period)
    if len(values) < period:
        return None
    window = _floats(values[-period:])
    return sum(window) / period


def ema_series(values: Sequence[float], period: int) -> list[float | None]:
    """指数移动平均序列。前 ``period - 1`` 项为 ``None``（预热）。

    以前 ``period`` 根的 SMA 作为种子，之后按 ``alpha = 2 / (period + 1)`` 递推。
    """
    _check_period(period)
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for index in range(period, len(values)):
        prev = alpha * values[index] + (1 - alpha) * prev
        out[index] = prev
    return out


def ema(values: Sequence[Decimal], period: int) -> float | None:
    """最新一根的 EMA；样本不足返回 ``None``。"""
    series = ema_series(_floats(values), period)
    return series[-1] if series else None


def rsi(values: Sequence[Decimal], period: int = 14) -> float | None:
    """Wilder 平滑 RSI。需要至少 ``period + 1`` 根收盘价。"""
    _check_period(period)
    closes = _floats(values)
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for previous, current in itertools.pairwise(closes):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:], strict=False):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


@dataclass(frozen=True, slots=True)
class MacdValue:
    macd: float
    signal: float
    histogram: float


def macd(
    values: Sequence[Decimal],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> MacdValue | None:
    """MACD（DIF、DEA、柱）。样本不足返回 ``None``。"""
    if fast >= slow:
        raise ValueError("fast 必须小于 slow")
    closes = _floats(values)
    fast_series = ema_series(closes, fast)
    slow_series = ema_series(closes, slow)
    if fast_series[-1] is None or slow_series[-1] is None:
        return None
    dif_series: list[float] = []
    start = slow - 1
    for index in range(start, len(closes)):
        f_val = fast_series[index]
        s_val = slow_series[index]
        if f_val is None or s_val is None:  # pragma: no cover - 由 start 保证
            continue
        dif_series.append(f_val - s_val)
    dea_series = ema_series(dif_series, signal_period)
    dea = dea_series[-1]
    if dea is None:
        return None
    dif = dif_series[-1]
    return MacdValue(macd=dif, signal=dea, histogram=dif - dea)


def true_ranges(
    highs: Sequence[Decimal], lows: Sequence[Decimal], closes: Sequence[Decimal]
) -> list[float]:
    """真实波幅序列；首根以自身振幅计。"""
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs/lows/closes 长度必须一致")
    h = _floats(highs)
    lo = _floats(lows)
    c = _floats(closes)
    out: list[float] = []
    for index in range(len(h)):
        if index == 0:
            out.append(h[0] - lo[0])
            continue
        prev_close = c[index - 1]
        out.append(
            max(h[index] - lo[index], abs(h[index] - prev_close), abs(lo[index] - prev_close))
        )
    return out


def atr(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    period: int = 14,
) -> float | None:
    """Wilder 平滑 ATR。需要至少 ``period`` 根（首根 TR 依赖自身振幅）。"""
    _check_period(period)
    trs = true_ranges(highs, lows, closes)
    if len(trs) < period:
        return None
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


@dataclass(frozen=True, slots=True)
class BollingerValue:
    upper: float
    middle: float
    lower: float
    bandwidth: float
    percent_b: float | None


def bollinger(
    values: Sequence[Decimal], period: int = 20, num_std: float = 2.0
) -> BollingerValue | None:
    """布林带（总体标准差）。``percent_b`` 在上下轨重合时为 ``None``。"""
    _check_period(period)
    if len(values) < period:
        return None
    window = _floats(values[-period:])
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std = math.sqrt(variance)
    upper = middle + num_std * std
    lower = middle - num_std * std
    bandwidth = (upper - lower) / middle if middle else 0.0
    width = upper - lower
    last = window[-1]
    percent_b = (last - lower) / width if width > 0 else None
    return BollingerValue(
        upper=upper, middle=middle, lower=lower, bandwidth=bandwidth, percent_b=percent_b
    )


def volume_stats(volumes: Sequence[Decimal], period: int = 20) -> dict[str, float | None] | None:
    """成交量均值、最新值相对均值的倍数，以及最新值在窗口内的分位（0-1）。

    均值为 0 时 ``ratio_to_mean`` 为 ``None``，避免除零与非法 JSON。
    """
    _check_period(period)
    if len(volumes) < period:
        return None
    window = _floats(volumes[-period:])
    mean = sum(window) / period
    last = window[-1]
    rank = sum(1 for x in window if x <= last) / period
    return {
        "mean": mean,
        "last": last,
        "ratio_to_mean": (last / mean) if mean > 0 else None,
        "percentile": rank,
    }


def _check_period(period: int) -> None:
    if period < 1:
        raise ValueError("period 必须 >= 1")
