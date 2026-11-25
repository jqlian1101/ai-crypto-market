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

from aicrypto.domain.anomaly.derivatives_rules import (
    DerivativesThresholds,
    evaluate_basis_extreme,
    evaluate_funding_extreme,
    evaluate_funding_flip,
    evaluate_ls_skew,
    evaluate_oi_change,
    evaluate_price_oi_divergence,
)
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
class DerivativesInputs:
    """衍生品规则的输入（PRD 22.2.4）。

    每个字段都是**可选**的：``None`` 表示「该数据不可用」，
    对应规则会被标记为 ``skipped``，而不是按 0 处理。

    - ``oi_change_pct``：1h OI 变化率（**百分数**，3.0 表示 +3%）；
    - ``funding_rate``：当前资金费率（**小数**，0.0005 表示 0.05%）。
      优先用 WS ``markPrice`` 的预估费率（低延迟，L-05），
      缺失时回落到已结算的最新费率；
    - ``previous_funding_rate``：上一期已结算费率，用于翻号判定；
    - ``basis_pct``：Spot-Futures 基差（**小数**）；
    - ``basis_percentile``：基差的历史分位（0–1），**绝对基差**的分位；
    - ``skew_percentile``：多空比的历史分位（0–1）；
    - ``price_change_pct``：同窗口价格变化率（**百分数**）。
    """

    oi_change_pct: float | None = None
    funding_rate: float | None = None
    previous_funding_rate: float | None = None
    basis_pct: float | None = None
    basis_percentile: float | None = None
    skew_percentile: float | None = None
    price_change_pct: float | None = None


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
    derivatives: DerivativesInputs | None = None,
    derivatives_thresholds: DerivativesThresholds | None = None,
) -> EngineResult:
    """对各周期 K 线执行已启用的规则。

    ``bars`` 键为周期（如 ``"5m"``）。缺少某周期数据时该周期规则计入 ``skipped``。
    ``derivatives`` 为衍生品输入；为 ``None`` 时全部衍生品规则计入 ``skipped``。
    """
    disabled = {r.strip() for r in disabled_rules if r.strip()}
    result = EngineResult()

    for rule in sorted(UNIMPLEMENTED_1M_RULES):
        if rule not in disabled:
            # 未禁用却无实现：必须报告，而不是静默失效（29.2.1）。
            result.errors.append(RuleError(rule, "规则未实现但未被禁用"))

    def _run(rule_id: str, fn: Callable[[], AnomalyHit | None]) -> None:
        # 配置按基础 ID 禁用（如 PRICE_PCT_5M），同时屏蔽其 _CRIT 变体。
        # 判定用「去掉 _CRIT 后缀」的基础 ID，保证禁用基础名就能屏蔽其
        # CRITICAL 变体（``X`` -> ``X_CRIT``）。
        base_id = rule_id.removesuffix("_CRIT")
        if base_id in disabled or rule_id in disabled:
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

    _run_derivatives(
        result, disabled=disabled, inputs=derivatives, thresholds=derivatives_thresholds
    )

    return result


#: 衍生品规则 -> 所需输入字段。用于在输入缺失时标记 ``skipped``。
_DERIVATIVES_RULE_INPUTS: dict[str, tuple[str, ...]] = {
    "DER_OI_SURGE": ("oi_change_pct",),
    "DER_OI_SURGE_CRIT": ("oi_change_pct",),
    "DER_OI_FLUSH": ("oi_change_pct",),
    "DER_FUNDING_EXTREME": ("funding_rate",),
    "DER_FUNDING_EXTREME_CRIT": ("funding_rate",),
    "DER_FUNDING_FLIP": ("funding_rate", "previous_funding_rate"),
    "DER_BASIS_EXTREME": ("basis_pct", "basis_percentile"),
    "DER_LS_SKEW": ("skew_percentile",),
    "DER_PRICE_OI_DIVERGENCE": ("price_change_pct", "oi_change_pct"),
}


def _run_derivatives(
    result: EngineResult,
    *,
    disabled: set[str],
    inputs: DerivativesInputs | None,
    thresholds: DerivativesThresholds | None,
) -> None:
    """执行衍生品规则族（PRD 22.2.4）。

    **缺失输入必须标记 skipped**，不能按 0 处理——按 0 会让
    ``DER_OI_FLUSH``（−3%）在数据缺失时误报，或让
    ``DER_FUNDING_EXTREME`` 漏报。
    """
    if inputs is None:
        for rule_id in sorted(_DERIVATIVES_RULE_INPUTS):
            result.skipped.append(SkippedRule(rule_id, "NO_DERIVATIVES_DATA"))
        return

    def _run(rule_id: str, fn: Callable[[], AnomalyHit | None]) -> None:
        # 禁用按**基础 ID** 判定：禁用 ``X`` 同时屏蔽 ``X_CRIT``。
        # （旧的 ``f"{rule_id}_CRIT" in disabled`` 方向相反，
        #  会让禁用 ``DER_FUNDING_EXTREME`` 反而放行它的 CRIT 变体。）
        base_id = rule_id.removesuffix("_CRIT")
        if base_id in disabled or rule_id in disabled:
            result.skipped.append(SkippedRule(rule_id, "DISABLED_BY_CONFIG"))
            return
        try:
            hit = fn()
        except Exception as exc:  # 单规则失败不影响其他规则
            result.errors.append(RuleError(rule_id, str(exc)))
            return
        if hit is not None:
            result.hits.append(hit)

    # OI 激增 / 崩塌：一次判定可能产出两个不同规则（不同阈值级别），
    # 因此不走 _run 的单命中路径，而是分别判定。
    if inputs.oi_change_pct is None:
        for rule_id in ("DER_OI_SURGE", "DER_OI_SURGE_CRIT", "DER_OI_FLUSH"):
            result.skipped.append(SkippedRule(rule_id, "MISSING_OI_CHANGE"))
    else:
        for hit in evaluate_oi_change(oi_change_pct=inputs.oi_change_pct, thresholds=thresholds):
            # 禁用按**基础 ID** 判定：禁用 ``DER_OI_SURGE`` 同时屏蔽
            # 它的 ``_CRIT`` 变体 ``DER_OI_SURGE_CRIT``。
            base_id = hit.rule_id.removesuffix("_CRIT")
            if base_id in disabled or hit.rule_id in disabled:
                result.skipped.append(SkippedRule(hit.rule_id, "DISABLED_BY_CONFIG"))
                continue
            result.hits.append(hit)

    _run(
        "DER_FUNDING_EXTREME",
        lambda: evaluate_funding_extreme(funding_rate=inputs.funding_rate, thresholds=thresholds),
    )
    _run(
        "DER_FUNDING_FLIP",
        lambda: evaluate_funding_flip(
            funding_rate=inputs.funding_rate,
            previous_rate=inputs.previous_funding_rate,
        ),
    )
    _run(
        "DER_BASIS_EXTREME",
        lambda: evaluate_basis_extreme(
            basis_pct=inputs.basis_pct,
            percentile=inputs.basis_percentile,
            thresholds=thresholds,
        ),
    )
    _run(
        "DER_LS_SKEW",
        lambda: evaluate_ls_skew(skew_percentile=inputs.skew_percentile, thresholds=thresholds),
    )
    _run(
        "DER_PRICE_OI_DIVERGENCE",
        lambda: evaluate_price_oi_divergence(
            price_change_pct=inputs.price_change_pct,
            oi_change_pct=inputs.oi_change_pct,
            thresholds=thresholds,
        ),
    )

    # 输入缺失但规则未命中时，明确标记 skipped（而非静默无输出）。
    hit_ids = {hit.rule_id for hit in result.hits}
    for rule_id, fields in _DERIVATIVES_RULE_INPUTS.items():
        if any(getattr(inputs, field) is None for field in fields) and rule_id not in hit_ids:
            result.skipped.append(SkippedRule(rule_id, "MISSING_INPUT"))


__all__ = [
    "PRICE_RULE_IDS",
    "UNIMPLEMENTED_1M_RULES",
    "BarWindow",
    "DerivativesInputs",
    "EngineResult",
    "RuleError",
    "SkippedRule",
    "run_engine",
]
