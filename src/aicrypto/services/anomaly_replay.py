"""异动规则历史回放（PRD 36.5）。

按时间逐根推进已收盘 K 线，每个时刻调用与线上一致的规则引擎
（``run_engine``）与去重/冷却判定（``decide_delivery``），统计每条规则的
触发次数、触发时刻、级别与被抑制次数，输出可比对的基线结构。

约束：

- 只读已收盘 K 线，每个时刻的输入不包含该时刻之后的数据（无未来函数）；
- 不做 IO：K 线由调用方注入，便于测试与离线生成基线；
- 指纹与冷却键复用 ``identity`` 模块，与线上扫描口径一致。
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from aicrypto.domain.anomaly.dedup import decide_delivery
from aicrypto.domain.anomaly.engine import BarWindow, run_engine
from aicrypto.domain.anomaly.identity import (
    build_cooldown_key,
    build_fingerprint,
)
from aicrypto.domain.anomaly.rules import (
    PriceMoveThresholds,
    VolatilityThresholds,
    VolumeThresholds,
)

BASELINE_BARS = 160
REPLAY_MARKET_TYPE = "USDM_FUTURES"


@dataclass(frozen=True, slots=True)
class Candle:
    open_time: dt.datetime
    close_time: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class ReplayFire:
    """一次被投递（未被抑制）的触发记录。"""

    symbol: str
    rule_id: str
    severity: str
    fired_at: dt.datetime
    window_start: dt.datetime


@dataclass(slots=True)
class ReplayReport:
    fires: list[ReplayFire] = field(default_factory=list)
    suppressed: dict[str, dict[str, int]] = field(default_factory=dict)
    skipped_rules: dict[str, str] = field(default_factory=dict)
    error_count: int = 0


def _window(candles: Sequence[Candle], timeframe: str) -> BarWindow:
    return BarWindow(
        timeframe=timeframe,
        opens=[c.open for c in candles],
        highs=[c.high for c in candles],
        lows=[c.low for c in candles],
        closes=[c.close for c in candles],
        volumes=[c.volume for c in candles],
    )


def replay_symbol(
    *,
    symbol: str,
    candles_by_tf: Mapping[str, Sequence[Candle]],
    disabled_rules: Sequence[str],
    price_thresholds: PriceMoveThresholds | None = None,
    volatility_thresholds: VolatilityThresholds | None = None,
    volume_thresholds: VolumeThresholds | None = None,
    step_timeframe: str = "5m",
) -> ReplayReport:
    """以 ``step_timeframe`` 的每根已收盘 K 线为时刻推进回放。

    在时刻 ``t`` 上，各周期只可见 ``close_time <= t`` 的 K 线。
    """
    report = ReplayReport()
    delivered: set[str] = set()
    last_fired: dict[str, dt.datetime] = {}
    steps = candles_by_tf[step_timeframe]

    for step in steps:
        cutoff = step.close_time
        bars: dict[str, BarWindow] = {}
        window_starts: dict[str, dt.datetime] = {}
        for tf, candles in candles_by_tf.items():
            visible = [c for c in candles if c.close_time <= cutoff]
            visible = visible[-BASELINE_BARS:]
            if not visible:
                continue
            bars[tf] = _window(visible, tf)
            window_starts[tf] = visible[-1].open_time

        result = run_engine(
            bars=bars,
            disabled_rules=disabled_rules,
            price_thresholds=price_thresholds,
            volatility_thresholds=volatility_thresholds,
            volume_thresholds=volume_thresholds,
        )
        report.error_count += len(result.errors)
        for skipped in result.skipped:
            report.skipped_rules.setdefault(skipped.rule_id, skipped.reason)

        for hit in result.hits:
            base_rule = hit.rule_id.removesuffix("_CRIT")
            tf = _timeframe_of(base_rule)
            window_start = window_starts.get(tf, cutoff)
            fingerprint = build_fingerprint(
                symbol=symbol,
                market_type=REPLAY_MARKET_TYPE,
                rule_id=hit.rule_id,
                window_start=window_start,
            )
            cooldown_key = build_cooldown_key(symbol=symbol, rule_id=hit.rule_id)
            decision = decide_delivery(
                fingerprint=fingerprint,
                revision=0,
                severity=hit.severity,
                cooldown_key=cooldown_key,
                now=cutoff,
                delivered_keys=delivered,
                last_fired_at=last_fired,
            )
            if not decision.allowed:
                bucket = report.suppressed.setdefault(hit.rule_id, defaultdict(int))
                bucket[decision.reason] += 1
                continue

            delivered.add(f"dedup:{fingerprint}:0")
            last_fired[cooldown_key] = cutoff
            report.fires.append(
                ReplayFire(
                    symbol=symbol,
                    rule_id=hit.rule_id,
                    severity=hit.severity,
                    fired_at=cutoff,
                    window_start=window_start,
                )
            )

    # 将 defaultdict 转为普通 dict，便于序列化与比对。
    report.suppressed = {k: dict(v) for k, v in report.suppressed.items()}
    return report


def _timeframe_of(base_rule_id: str) -> str:
    if base_rule_id.startswith("PRICE_PCT_5M"):
        return "5m"
    if base_rule_id.startswith("PRICE_PCT_15M"):
        return "15m"
    if base_rule_id.startswith("PRICE_PCT_1H"):
        return "1h"
    return "1h"


__all__ = ["Candle", "ReplayFire", "ReplayReport", "replay_symbol"]
