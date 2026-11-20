"""异动规则纯函数（PRD 22.2.1 - 22.2.3）。

本模块只实现不依赖 1m 数据的规则：

- 价格：``PRICE_PCT_5M`` / ``15M`` / ``1H`` 及其 CRITICAL 版本；
- 波动：``VOL_ATR_SPIKE`` 及其 CRITICAL 版本；
- 成交量：``VOL_REL_VOLUME`` 及其 CRITICAL 版本。

1m 依赖规则（``PRICE_PCT_1M``、``PRICE_GAP``、``PRICE_FLASH_MOVE``）按 22.2 必须
显式禁用，不在本模块实现。

约束：只接受已收盘 K 线（29.5），调用方负责过滤；本模块不做 IO。
阈值默认值取 PRD 22.2 初始值，可通过 dataclass 覆盖（配置化由调用方完成）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from aicrypto.domain.market.indicators import true_ranges


@dataclass(frozen=True, slots=True)
class AnomalyHit:
    """单条规则命中结果。``threshold`` 用于事件快照（22.3，保证可复现）。"""

    rule_id: str
    severity: str  # "WARN" | "CRITICAL"
    direction: str  # "UP" | "DOWN" | "NEUTRAL"
    metrics: dict[str, float | None] = field(default_factory=dict)
    threshold: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PriceMoveThresholds:
    """22.2.1 绝对涨跌幅阈值（%）。键为周期。"""

    warn_pct: dict[str, float] = field(
        default_factory=lambda: {"5m": 0.60, "15m": 1.20, "1h": 2.50}
    )
    critical_pct: dict[str, float] = field(
        default_factory=lambda: {"5m": 1.20, "15m": 2.50, "1h": 4.50}
    )


@dataclass(frozen=True, slots=True)
class VolatilityThresholds:
    """22.2.2 ATR 突变倍数：当前 ATR(14) 相对 96 根均值。"""

    warn_ratio: float = 1.8
    critical_ratio: float = 2.5
    atr_period: int = 14
    baseline_bars: int = 96


@dataclass(frozen=True, slots=True)
class VolumeThresholds:
    """22.2.3 相对成交量倍数：当前成交量相对同周期历史均值。"""

    warn_ratio: float = 3.0
    critical_ratio: float = 6.0
    baseline_bars: int = 20


def bar_return_pct(open_price: Decimal, close_price: Decimal) -> float | None:
    """单根 K 线的实体涨跌幅（%）。开盘价为 0 时返回 None。"""
    if open_price == 0:
        return None
    return float((close_price - open_price) / open_price * 100)


def _direction(change: float) -> str:
    if change > 0:
        return "UP"
    if change < 0:
        return "DOWN"
    return "NEUTRAL"


def evaluate_price_move(
    *,
    timeframe: str,
    window_open: Decimal,
    window_close: Decimal,
    thresholds: PriceMoveThresholds | None = None,
) -> AnomalyHit | None:
    """价格异动：窗口内（开盘价 → 收盘价）的绝对涨跌幅。

    ``window_open`` / ``window_close`` 为触发窗口的首根开盘与末根收盘。
    同时满足 CRITICAL 阈值时返回 CRITICAL，否则满足 WARN 返回 WARN。
    不支持的周期抛 ValueError（例如 1m 须显式禁用，而非静默跳过）。
    """
    limits = thresholds or PriceMoveThresholds()
    if timeframe not in limits.warn_pct:
        raise ValueError(f"价格异动不支持周期 {timeframe!r}（1m 依赖规则须显式禁用）")
    change = bar_return_pct(window_open, window_close)
    if change is None:
        return None
    magnitude = abs(change)
    warn = limits.warn_pct[timeframe]
    critical = limits.critical_pct[timeframe]
    if magnitude >= critical:
        severity = "CRITICAL"
    elif magnitude >= warn:
        severity = "WARN"
    else:
        return None
    upper = timeframe.upper()
    return AnomalyHit(
        rule_id=f"PRICE_PCT_{upper}" + ("_CRIT" if severity == "CRITICAL" else ""),
        severity=severity,
        direction=_direction(change),
        metrics={"price_change_pct": round(change, 6)},
        threshold={"price_change_pct": critical if severity == "CRITICAL" else warn},
    )


def atr_series(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    period: int = 14,
) -> list[float | None]:
    """逐根 Wilder ATR 序列；前 ``period - 1`` 根为 None（预热）。

    只在已有数据上递推，不引用未来 K 线。
    """
    trs = true_ranges(highs, lows, closes)
    out: list[float | None] = [None] * len(trs)
    if len(trs) < period:
        return out
    value = sum(trs[:period]) / period
    out[period - 1] = value
    for index in range(period, len(trs)):
        value = (value * (period - 1) + trs[index]) / period
        out[index] = value
    return out


def evaluate_volatility(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    *,
    thresholds: VolatilityThresholds | None = None,
) -> AnomalyHit | None:
    """ATR 突变：最新 ATR 相对此前 ``baseline_bars`` 根 ATR 均值的倍数。

    基线不包含当前根（避免自我稀释）。样本不足返回 None。
    """
    limits = thresholds or VolatilityThresholds()
    series = atr_series(highs, lows, closes, limits.atr_period)
    current = series[-1] if series else None
    history = [v for v in series[:-1] if v is not None][-limits.baseline_bars :]
    if current is None or len(history) < limits.baseline_bars:
        return None
    baseline = sum(history) / len(history)
    if baseline <= 0:
        return None
    ratio = current / baseline
    if ratio >= limits.critical_ratio:
        severity = "CRITICAL"
    elif ratio >= limits.warn_ratio:
        severity = "WARN"
    else:
        return None
    return AnomalyHit(
        rule_id="VOL_ATR_SPIKE" + ("_CRIT" if severity == "CRITICAL" else ""),
        severity=severity,
        direction="NEUTRAL",
        metrics={"atr_ratio": round(ratio, 6), "atr": round(current, 8)},
        threshold={
            "atr_ratio": limits.critical_ratio if severity == "CRITICAL" else limits.warn_ratio
        },
    )


def evaluate_volume(
    volumes: Sequence[Decimal],
    closes: Sequence[Decimal],
    opens: Sequence[Decimal],
    *,
    thresholds: VolumeThresholds | None = None,
) -> AnomalyHit | None:
    """相对成交量：最新成交量相对前 ``baseline_bars`` 根均值的倍数。

    方向取最新根实体方向。样本不足或基线为 0 时返回 None。
    """
    limits = thresholds or VolumeThresholds()
    if len(volumes) < limits.baseline_bars + 1:
        return None
    history = [float(v) for v in volumes[-(limits.baseline_bars + 1) : -1]]
    baseline = sum(history) / len(history)
    if baseline <= 0:
        return None
    current = float(volumes[-1])
    ratio = current / baseline
    if ratio >= limits.critical_ratio:
        severity = "CRITICAL"
    elif ratio >= limits.warn_ratio:
        severity = "WARN"
    else:
        return None
    change = bar_return_pct(opens[-1], closes[-1]) or 0.0
    return AnomalyHit(
        rule_id="VOL_REL_VOLUME" + ("_CRIT" if severity == "CRITICAL" else ""),
        severity=severity,
        direction=_direction(change),
        metrics={"relative_volume": round(ratio, 6), "volume": current},
        threshold={
            "relative_volume": limits.critical_ratio
            if severity == "CRITICAL"
            else limits.warn_ratio
        },
    )
