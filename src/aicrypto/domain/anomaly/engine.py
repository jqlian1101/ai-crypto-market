"""异动规则引擎：组合规则纯函数，执行 ``disabled_rules``（PRD 22.2 / 29.2.1）。

职责边界：

- 只接收已收盘 K 线（29.5），不做 IO；
- 被禁用的规则**显式记入 ``skipped``**，而不是静默丢弃，便于审计；
- 规则之间相互独立，单条规则抛错不影响其他规则（错误计入 ``errors``）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from aicrypto.domain.anomaly.rules import (
    AnomalyHit,
    PriceMoveThresholds,
    VolatilityThresholds,
    VolumeThresholds,
    evaluate_price_move,
    evaluate_volatility,
    evaluate_volume,
)

# 规则 ID 与配置 ``anomaly.rules.disabled_rules`` 对齐。
# 1m 依赖规则当前在域层未实现，必须出现在禁用列表中（22.2 / 29.2.1）。
PRICE_RULE_IDS: dict[str, str] = {
    "5m": "PRICE_PCT_5M",
    "15m": "PRICE_PCT_15M",
    "1h": "PRICE_PCT_1H",
}
UNIMPLEMENTED_1M_RULES = frozenset(
    {"PRICE_PCT_1M", "PRICE_PCT_1M_CRIT", "PRICE_GAP", "PRICE_FLASH_MOVE"}
)


@dataclass(frozen=True, slots=True)
class BarWindow:
    """某周期的已收盘 K 线序列（时间升序，最后一根为最新已收盘）。"""

    timeframe: str
    opens: Sequence[Decimal]
    highs: Sequence[Decimal]
    lows: Sequence[Decimal]
    closes: Sequence[Decimal]
    volumes: Sequence[Decimal]


@dataclass(frozen=True, slots=True)
class SkippedRule:
    rule_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RuleError:
    rule_id: str
    message: str


@dataclass(slots=True)
class EngineResult:
    hits: list[AnomalyHit] = field(default_factory=list)
    skipped: list[SkippedRule] = field(default_factory=list)
    errors: list[RuleError] = field(default_factory=list)


def run_engine(
    *,
    bars: Mapping[str, BarWindow],
    disabled_rules: Iterable[str],
    price_thresholds: PriceMoveThresholds | None = None,
    volatility_thresholds: VolatilityThresholds | None = None,
    volume_thresholds: VolumeThresholds | None = None,
) -> EngineResult:
    """对各周期 K 线执行已启用的规则。

    ``bars`` 键为周期（如 ``"5m"``）。缺少某周期数据时该周期规则计入 ``skipped``。
    """
    disabled = {r.strip() for r in disabled_rules if r.strip()}
    result = EngineResult()

    for rule in sorted(UNIMPLEMENTED_1M_RULES):
        if rule not in disabled:
            # 未禁用却无实现：必须报告，而不是静默失效（29.2.1）。
            result.errors.append(RuleError(rule, "规则未实现但未被禁用"))

    def _run(rule_id: str, fn: Callable[[], AnomalyHit | None]) -> None:
        # 配置按基础 ID 禁用（如 PRICE_PCT_5M），同时屏蔽其 _CRIT 变体。
        if rule_id in disabled or f"{rule_id}_CRIT" in disabled:
            result.skipped.append(SkippedRule(rule_id, "DISABLED_BY_CONFIG"))
            return
        try:
            hit = fn()
        except Exception as exc:  # 单规则失败不影响其他规则
            result.errors.append(RuleError(rule_id, str(exc)))
            return
        if hit is not None:
            result.hits.append(hit)

    for timeframe, rule_id in PRICE_RULE_IDS.items():
        window = bars.get(timeframe)
        if window is None or not window.closes:
            result.skipped.append(SkippedRule(rule_id, "NO_BARS"))
            continue

        def _price(w: BarWindow = window) -> AnomalyHit | None:
            return evaluate_price_move(
                timeframe=w.timeframe,
                window_open=w.opens[-1],
                window_close=w.closes[-1],
                thresholds=price_thresholds,
            )

        _run(rule_id, _price)

    # 波动与成交量规则使用 1h 周期（22.2.2 / 22.2.3）。
    hour = bars.get("1h")
    if hour is None:
        result.skipped.append(SkippedRule("VOL_ATR_SPIKE", "NO_BARS"))
        result.skipped.append(SkippedRule("VOL_REL_VOLUME", "NO_BARS"))
    else:
        _run(
            "VOL_ATR_SPIKE",
            lambda: evaluate_volatility(
                hour.highs, hour.lows, hour.closes, thresholds=volatility_thresholds
            ),
        )
        _run(
            "VOL_REL_VOLUME",
            lambda: evaluate_volume(
                hour.volumes, hour.closes, hour.opens, thresholds=volume_thresholds
            ),
        )

    return result


__all__ = [
    "PRICE_RULE_IDS",
    "UNIMPLEMENTED_1M_RULES",
    "BarWindow",
    "EngineResult",
    "RuleError",
    "SkippedRule",
    "run_engine",
]
