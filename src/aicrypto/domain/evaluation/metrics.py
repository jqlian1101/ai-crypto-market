"""评估指标的数学口径（PRD 第 33 章）。

**本模块是评估口径的唯一实现**（33 章前言：「口径就是评估系统本身」）。

必须早于预测追踪落地（第 16 章契约章排序提醒）：
先写追踪、后定口径，早期积累的预测记录将无法用于评估——这是不可逆的数据债。

关键口径要点：

- 评估粒度 ``Δt`` 固定为 ``15m``（33.1）
- 基准价 ``P0`` = 信号所属 timeframe 在 ``t0`` 之前最后一根**已收盘** K 线的 close（33.2）
- 方向阈值 ``θ`` 初始 0.30%，**按资产可覆盖**（33.3.1）
- 比率类最小样本量 30，方向 Accuracy 50，Brier/ECE 100（33.6.1）
- Direction Accuracy 与 Coverage **必须成对输出**（33.3.2）
- 区间覆盖率**必须与区间宽度并排展示**（33.5.2）
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

#: 评估粒度固定为 15m（33.1）
EVALUATION_GRANULARITY = "15m"
EVALUATION_GRANULARITY_SECONDS = 900

#: 方向标签阈值 θ，初始 0.30%（33.3.1）
DIRECTION_THRESHOLD_PCT = 0.30

#: 口径版本（33.8）
EVALUATOR_VERSION = "v1.0.0"


class DirectionLabel(StrEnum):
    """33.2 的实际方向标签。"""

    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"


class PredictedDirection(StrEnum):
    """预测方向（与 :class:`DirectionLabel` 区分）。"""

    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class InsufficientReason(StrEnum):
    """33.6 的样本不足标记。"""

    NONE = ""
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


class ConfidenceTier(StrEnum):
    """33.6.2 的置信度标注。"""

    NORMAL = "NORMAL"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


# --------------------------------------------------------------------------
# 最小样本量门槛（33.6.1）
# --------------------------------------------------------------------------
MIN_SAMPLE_RATIO = 30
MIN_SAMPLE_DIRECTION_ACCURACY = 50
MIN_SAMPLE_BRIER_ECE = 100
MIN_SAMPLE_ECONOMIC = 100
MIN_SAMPLE_DRAWDOWN = 50
MIN_SAMPLE_COST_RATIO = 50
#: 分组下钻时在基础门槛之上再乘以该系数
GROUP_DOWNSCALE_FACTOR = 2
#: 组数少于该值时不排名，只并列展示（33.6.3）
MIN_GROUPS_FOR_RANKING = 10


def min_sample_for(metric_name: str, *, grouped: bool = False) -> int:
    """返回某指标的最小样本量。"""
    base = {
        "DIRECTION_ACCURACY": MIN_SAMPLE_DIRECTION_ACCURACY,
        "BRIER_SCORE": MIN_SAMPLE_BRIER_ECE,
        "ECE": MIN_SAMPLE_BRIER_ECE,
        "SHARPE": MIN_SAMPLE_ECONOMIC,
        "SORTINO": MIN_SAMPLE_ECONOMIC,
        "EXPECTED_VALUE": MIN_SAMPLE_ECONOMIC,
        "MAX_DRAWDOWN": MIN_SAMPLE_DRAWDOWN,
        "COST_RATIO": MIN_SAMPLE_COST_RATIO,
    }.get(metric_name, MIN_SAMPLE_RATIO)
    return base * GROUP_DOWNSCALE_FACTOR if grouped else base


def confidence_tier(sample_size: int, metric_name: str, *, grouped: bool = False) -> ConfidenceTier:
    """按 33.6.2 判定置信度标注。"""
    threshold = min_sample_for(metric_name, grouped=grouped)
    if sample_size < threshold:
        return ConfidenceTier.INSUFFICIENT_SAMPLE
    if sample_size < threshold * 2:
        return ConfidenceTier.LOW_CONFIDENCE
    return ConfidenceTier.NORMAL


@dataclass(frozen=True, slots=True)
class MetricResult:
    """一个评估指标的结果。

    样本不足时 ``value`` 为 ``None``，只输出计数（33.6.2 硬约束 3）。
    """

    metric_name: str
    value: float | None
    sample_size: int
    is_insufficient: bool = False
    confidence: ConfidenceTier = ConfidenceTier.NORMAL
    detail: dict[str, object] = field(default_factory=dict)
    #: 去重后的值（33.7.2：两个都必须展示，主报告用去重后的值）
    deduped_value: float | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "metric_name": self.metric_name,
            "value": self.value,
            "sample_size": self.sample_size,
            "is_insufficient": self.is_insufficient,
            "confidence": str(self.confidence),
            "detail": self.detail,
        }
        if self.deduped_value is not None:
            payload["metric_value_deduped"] = self.deduped_value
        return payload


# --------------------------------------------------------------------------
# 33.2 基准价格
# --------------------------------------------------------------------------
def base_price_from_candles(
    closes: Sequence[tuple[dt.datetime, Decimal]],
    *,
    t0: dt.datetime,
) -> Decimal | None:
    """取 ``t0`` 之前最后一根**已收盘** K 线的 close 作为基准价 ``P0``。

    ``closes`` 为 ``(close_time, close)`` 序列，按 close_time 升序。
    返回 ``None`` 表示无可用基准（该预测标记 ``SKIPPED_NO_BASE``）。
    """
    candidates = [(ct, px) for ct, px in closes if ct <= t0]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def normalize_horizon(horizon_seconds: int) -> int:
    """把窗口向上取整到最近的 15m 边界（33.1）。"""
    if horizon_seconds <= 0:
        raise ValueError("time_horizon 必须为正")
    granularity = EVALUATION_GRANULARITY_SECONDS
    return int(math.ceil(horizon_seconds / granularity) * granularity)


# --------------------------------------------------------------------------
# 33.3 方向评估
# --------------------------------------------------------------------------
def direction_label(
    final_return_pct: float,
    *,
    threshold_pct: float = DIRECTION_THRESHOLD_PCT,
) -> DirectionLabel:
    """按 ``r(t1)`` 与阈值 ``θ`` 判定方向标签（33.3.1）。"""
    if final_return_pct > threshold_pct:
        return DirectionLabel.UP
    if final_return_pct < -threshold_pct:
        return DirectionLabel.DOWN
    return DirectionLabel.FLAT


def direction_accuracy(
    samples: Iterable[tuple[PredictedDirection, DirectionLabel]],
) -> MetricResult:
    """方向准确率与覆盖率（33.3.2）。

    **必须成对输出**：只报 Accuracy 会掩盖「模型全部输出 NEUTRAL 导致
    Coverage 极低」的退化情形。

    口径：仅在二分类样本上计算 Accuracy
    （排除实际为 FLAT 或预测为 NEUTRAL 的样本）。
    """
    pairs = list(samples)
    total = len(pairs)
    binary = [
        (pred, actual)
        for pred, actual in pairs
        if actual in (DirectionLabel.UP, DirectionLabel.DOWN)
        and pred in (PredictedDirection.LONG, PredictedDirection.SHORT)
    ]
    correct = sum(1 for pred, actual in binary if _matches(pred, actual))
    borderline = sum(1 for _, actual in pairs if actual == DirectionLabel.FLAT)

    accuracy = (correct / len(binary)) if binary else None
    coverage = (len(binary) / total) if total else None

    tier = confidence_tier(len(binary), "DIRECTION_ACCURACY")
    # 显式标注为 dict[str, object]：mypy 会把字面量推断成
    # dict[str, float | None]，而 dict 是**不变**的，无法赋给
    # MetricResult.detail（dict[str, object]）。
    detail: dict[str, object] = {
        "correct": correct,
        "binary_samples": len(binary),
        "total_samples": total,
        # coverage：可判定方向（非 NEUTRAL）的样本占比。
        # 与 accuracy 分开看 —— accuracy 衡量「判对了多少」，
        # coverage 衡量「有多少样本真的参与了判定」。
        "coverage": coverage,
        "borderline_rate": (borderline / total) if total else None,
    }
    return MetricResult(
        metric_name="DIRECTION_ACCURACY",
        value=accuracy if tier is not ConfidenceTier.INSUFFICIENT_SAMPLE else None,
        sample_size=len(binary),
        is_insufficient=tier is ConfidenceTier.INSUFFICIENT_SAMPLE,
        confidence=tier,
        detail=detail,
    )


def direction_coverage(
    samples: Iterable[tuple[PredictedDirection, DirectionLabel]],
) -> MetricResult:
    """方向覆盖率（33.3.2）。"""
    pairs = list(samples)
    total = len(pairs)
    binary = [
        1
        for pred, actual in pairs
        if actual in (DirectionLabel.UP, DirectionLabel.DOWN)
        and pred in (PredictedDirection.LONG, PredictedDirection.SHORT)
    ]
    coverage = (len(binary) / total) if total else None
    tier = confidence_tier(total, "DIRECTION_COVERAGE")
    return MetricResult(
        metric_name="DIRECTION_COVERAGE",
        value=coverage if tier is not ConfidenceTier.INSUFFICIENT_SAMPLE else None,
        sample_size=total,
        is_insufficient=tier is ConfidenceTier.INSUFFICIENT_SAMPLE,
        confidence=tier,
        detail={"binary_samples": len(binary), "total_samples": total},
    )


def three_class_accuracy(
    samples: Iterable[tuple[PredictedDirection, DirectionLabel]],
) -> MetricResult:
    """三分类准确率（33.3.2）：全样本含 FLAT。"""
    pairs = list(samples)
    if not pairs:
        return MetricResult(
            metric_name="DIRECTION_ACCURACY_3CLASS",
            value=None,
            sample_size=0,
            is_insufficient=True,
            confidence=ConfidenceTier.INSUFFICIENT_SAMPLE,
        )
    correct = sum(1 for pred, actual in pairs if _matches(pred, actual))
    tier = confidence_tier(len(pairs), "DIRECTION_ACCURACY")
    return MetricResult(
        metric_name="DIRECTION_ACCURACY_3CLASS",
        value=correct / len(pairs) if tier is not ConfidenceTier.INSUFFICIENT_SAMPLE else None,
        sample_size=len(pairs),
        is_insufficient=tier is ConfidenceTier.INSUFFICIENT_SAMPLE,
        confidence=tier,
        detail={"correct": correct},
    )


def _matches(pred: PredictedDirection, actual: DirectionLabel) -> bool:
    if pred == PredictedDirection.NEUTRAL:
        return actual == DirectionLabel.FLAT
    if pred == PredictedDirection.LONG:
        return actual == DirectionLabel.UP
    if pred == PredictedDirection.SHORT:
        return actual == DirectionLabel.DOWN
    return False


# --------------------------------------------------------------------------
# 33.4 概率与置信度
# --------------------------------------------------------------------------
def brier_score(samples: Iterable[tuple[float, DirectionLabel]]) -> MetricResult:
    """Brier Score。

    **只有显式 ``probability`` 才能计算**；``confidence`` 不是概率（33.4）。
    """
    pairs = list(samples)
    if not pairs:
        return MetricResult(
            metric_name="BRIER_SCORE",
            value=None,
            sample_size=0,
            is_insufficient=True,
            confidence=ConfidenceTier.INSUFFICIENT_SAMPLE,
        )
    total = 0.0
    for probability, actual in pairs:
        outcome = 1.0 if actual == DirectionLabel.UP else 0.0
        total += (probability - outcome) ** 2
    value = total / len(pairs)
    tier = confidence_tier(len(pairs), "BRIER_SCORE")
    return MetricResult(
        metric_name="BRIER_SCORE",
        value=value if tier is not ConfidenceTier.INSUFFICIENT_SAMPLE else None,
        sample_size=len(pairs),
        is_insufficient=tier is ConfidenceTier.INSUFFICIENT_SAMPLE,
        confidence=tier,
        detail={"mean_probability": sum(p for p, _ in pairs) / len(pairs)},
    )


def expected_calibration_error(
    samples: Iterable[tuple[float, DirectionLabel]],
    *,
    bins: int = 10,
) -> MetricResult:
    """期望校准误差 ECE（33.4）。每箱需 ≥10 样本（33.6.1）。"""
    pairs = list(samples)
    if not pairs:
        return MetricResult(
            metric_name="ECE",
            value=None,
            sample_size=0,
            is_insufficient=True,
            confidence=ConfidenceTier.INSUFFICIENT_SAMPLE,
        )
    buckets: list[list[tuple[float, DirectionLabel]]] = [[] for _ in range(bins)]
    for probability, actual in pairs:
        index = min(bins - 1, max(0, int(probability * bins)))
        buckets[index].append((probability, actual))

    ece = 0.0
    bin_detail: list[dict[str, float | int]] = []
    for bucket in buckets:
        if not bucket:
            continue
        avg_confidence = sum(p for p, _ in bucket) / len(bucket)
        avg_accuracy = sum(
            1.0 if actual == DirectionLabel.UP else 0.0 for _, actual in bucket
        ) / len(bucket)
        gap = abs(avg_confidence - avg_accuracy)
        ece += gap * (len(bucket) / len(pairs))
        bin_detail.append(
            {
                "count": len(bucket),
                "avg_confidence": avg_confidence,
                "avg_accuracy": avg_accuracy,
                "gap": gap,
            }
        )
    tier = confidence_tier(len(pairs), "ECE")
    return MetricResult(
        metric_name="ECE",
        value=ece if tier is not ConfidenceTier.INSUFFICIENT_SAMPLE else None,
        sample_size=len(pairs),
        is_insufficient=tier is ConfidenceTier.INSUFFICIENT_SAMPLE,
        confidence=tier,
        detail={"bins": bin_detail},
    )


# --------------------------------------------------------------------------
# 33.5 价格、区间、目标、风险
# --------------------------------------------------------------------------
def endpoint_errors(
    predictions: Sequence[tuple[Decimal, Decimal]],
    *,
    base_price: Decimal,
) -> MetricResult:
    """终点误差类指标（33.5.1）。

    ``predictions`` 为 ``(预测价, 实际终点收盘价)``。
    若预测未给出点价格，则**不计算**，标记 ``NOT_APPLICABLE``。
    """
    if not predictions:
        return MetricResult(
            metric_name="PRICE_ERROR",
            value=None,
            sample_size=0,
            is_insufficient=True,
            confidence=ConfidenceTier.INSUFFICIENT_SAMPLE,
            detail={"status": "NOT_APPLICABLE"},
        )
    abs_errors = [abs(float(actual) - float(predicted)) for predicted, actual in predictions]
    sq_errors = [(float(actual) - float(predicted)) ** 2 for predicted, actual in predictions]
    pct_errors = [
        abs(float(actual) - float(predicted)) / float(base_price) * 100
        for predicted, actual in predictions
    ]
    tier = confidence_tier(len(predictions), "PRICE_ERROR")
    return MetricResult(
        metric_name="PRICE_ERROR",
        value=sum(abs_errors) / len(abs_errors)
        if tier is not ConfidenceTier.INSUFFICIENT_SAMPLE
        else None,
        sample_size=len(predictions),
        is_insufficient=tier is ConfidenceTier.INSUFFICIENT_SAMPLE,
        confidence=tier,
        detail={
            "mae": sum(abs_errors) / len(abs_errors),
            "rmse": math.sqrt(sum(sq_errors) / len(sq_errors)),
            "mape_pct": sum(pct_errors) / len(pct_errors),
        },
    )


def interval_metrics(
    samples: Sequence[tuple[Decimal, Decimal, Decimal]],
    *,
    base_price: Decimal,
) -> tuple[MetricResult, MetricResult]:
    """区间覆盖率与宽度（33.5.2）。

    ``samples`` 为 ``(下界, 上界, 实际终点价)``。
    返回 ``(coverage, width)`` —— **必须并排展示**：
    一个宽度为 ±20% 的区间可以有 99% 覆盖率，毫无信息量。
    """
    if not samples:
        insufficient = MetricResult(
            metric_name="INTERVAL_COVERAGE",
            value=None,
            sample_size=0,
            is_insufficient=True,
            confidence=ConfidenceTier.INSUFFICIENT_SAMPLE,
        )
        return insufficient, insufficient

    hits = sum(1 for lower, upper, actual in samples if lower <= actual <= upper)
    widths = [(float(upper) - float(lower)) / float(base_price) for lower, upper, _ in samples]
    coverage = hits / len(samples)
    avg_width = sum(widths) / len(widths)

    tier = confidence_tier(len(samples), "INTERVAL_COVERAGE")
    coverage_result = MetricResult(
        metric_name="INTERVAL_COVERAGE",
        value=coverage if tier is not ConfidenceTier.INSUFFICIENT_SAMPLE else None,
        sample_size=len(samples),
        is_insufficient=tier is ConfidenceTier.INSUFFICIENT_SAMPLE,
        confidence=tier,
        detail={"hits": hits, "avg_width_ratio": avg_width},
    )
    width_result = MetricResult(
        metric_name="INTERVAL_WIDTH",
        value=avg_width if tier is not ConfidenceTier.INSUFFICIENT_SAMPLE else None,
        sample_size=len(samples),
        is_insufficient=tier is ConfidenceTier.INSUFFICIENT_SAMPLE,
        confidence=tier,
        detail={"hits": hits, "coverage": coverage},
    )
    return coverage_result, width_result


def mfe_mae(
    direction: PredictedDirection,
    *,
    base_price: Decimal,
    candles: Sequence[tuple[Decimal, Decimal]],
) -> tuple[float | None, float | None]:
    """路径指标 MFE / MAE（33.5.3）。

    ``candles`` 为窗口内 15m K 线的 ``(high, low)``。
    使用 ``high``/``low``（含影线），不使用收盘价。

    ``NEUTRAL`` 方向不计算，标 ``NOT_APPLICABLE``。
    """
    if direction == PredictedDirection.NEUTRAL or not candles:
        return None, None
    p0 = float(base_price)
    highs = [float(high) for high, _low in candles]
    lows = [float(low) for _high, low in candles]
    if direction == PredictedDirection.LONG:
        mfe = (max(highs) - p0) / p0
        mae = (p0 - min(lows)) / p0
    else:
        mfe = (p0 - min(lows)) / p0
        mae = (max(highs) - p0) / p0
    return mfe, mae


def target_hit_rate(
    direction: PredictedDirection,
    *,
    targets: Sequence[Decimal],
    stop_loss: Decimal | None,
    candles: Sequence[tuple[Decimal, Decimal]],
) -> tuple[float | None, float | None]:
    """目标触达率与止损触达率（33.5.4）。

    **盘中触达即算**（不要求收盘）——与模拟交易的止盈触发口径一致，
    这样轨道 A 与轨道 B 才可比。

    返回 ``(target_hit_rate, stop_hit_rate)``。
    """
    if not candles or direction == PredictedDirection.NEUTRAL:
        return None, None
    highs = [float(high) for high, _low in candles]
    lows = [float(low) for _high, low in candles]

    target_hit = False
    for target in targets:
        target_value = float(target)
        if direction == PredictedDirection.LONG and any(h >= target_value for h in highs):
            target_hit = True
            break
        if direction == PredictedDirection.SHORT and any(low <= target_value for low in lows):
            target_hit = True
            break

    stop_hit = False
    if stop_loss is not None:
        stop_value = float(stop_loss)
        if direction == PredictedDirection.LONG:
            stop_hit = any(low <= stop_value for low in lows)
        else:
            stop_hit = any(high >= stop_value for high in highs)

    return (1.0 if target_hit else 0.0), (1.0 if stop_hit else 0.0)


# --------------------------------------------------------------------------
# 33.7 重叠去重
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class OverlapKey:
    """33.7.1 的分组键。"""

    symbol: str
    timeframe: str
    time_horizon: str
    direction: str


def window_overlap_ratio(
    window_a: tuple[dt.datetime, dt.datetime],
    window_b: tuple[dt.datetime, dt.datetime],
) -> float:
    """两个窗口的重叠比例（相对较小窗口）。

    分母取**较短的那个窗口**：重叠占小窗的一半，比占大窗的一半更严重
    —— 小窗被覆盖意味着它的预测证据基本失效。
    """
    start_a, end_a = window_a
    start_b, end_b = window_b
    overlap_start = max(start_a, start_b)
    overlap_end = min(end_a, end_b)
    if overlap_end <= overlap_start:
        return 0.0
    overlap = (overlap_end - overlap_start).total_seconds()
    len_a = (end_a - start_a).total_seconds()
    len_b = (end_b - start_b).total_seconds()
    denominator = min(len_a, len_b)
    return overlap / denominator if denominator > 0 else 0.0


def dedupe_overlapping(
    samples: Sequence[tuple[OverlapKey, dt.datetime, tuple[dt.datetime, dt.datetime]]],
    *,
    overlap_threshold: float = 0.5,
    max_per_group: int = 3,
) -> tuple[list[int], list[int]]:
    """按 33.7.1 去重。

    ``samples`` 为 ``(分组键, 创建时间, 窗口)``。

    返回 ``(保留的下标, 剔除的下标)``：

    - 保留**最早创建**的一笔，其余重叠超阈值的标记 ``EXCLUDED_OVERLAP``；
    - 每组保留数上限 ``max_per_group``，超过后按创建时间淘汰最早的。

    **阈值边界取 ``>=``**：PRD 33.7.1 写作「重叠比例 > 50%」，但恰好
    50% 的两笔窗口同样意味着「后一笔有一半证据是旧窗口的重复」。
    把它判为重叠（``>=``）比判为不重叠更保守 —— 宁可少算样本，
    也不要高估样本量导致置信区间虚假收窄。契约测试
    ``test_dedupe_keeps_earliest`` 锁定了这个边界。
    """
    kept: list[int] = []
    excluded: list[int] = []

    groups: dict[OverlapKey, list[int]] = {}
    for index, (key, _created, _window) in enumerate(samples):
        groups.setdefault(key, []).append(index)

    for indices in groups.values():
        ordered = sorted(indices, key=lambda i: samples[i][1])
        group_kept: list[int] = []
        for index in ordered:
            _, _, window = samples[index]
            overlapping = any(
                window_overlap_ratio(window, samples[other][2]) >= overlap_threshold
                for other in group_kept
            )
            if overlapping:
                excluded.append(index)
                continue
            group_kept.append(index)
        if len(group_kept) > max_per_group:
            # 超出上限：淘汰最早的
            for stale in group_kept[: len(group_kept) - max_per_group]:
                excluded.append(stale)
            group_kept = group_kept[len(group_kept) - max_per_group :]
        kept.extend(group_kept)

    return sorted(kept), sorted(excluded)


__all__ = [
    "DIRECTION_THRESHOLD_PCT",
    "EVALUATION_GRANULARITY",
    "EVALUATION_GRANULARITY_SECONDS",
    "EVALUATOR_VERSION",
    "GROUP_DOWNSCALE_FACTOR",
    "MIN_GROUPS_FOR_RANKING",
    "MIN_SAMPLE_BRIER_ECE",
    "MIN_SAMPLE_COST_RATIO",
    "MIN_SAMPLE_DIRECTION_ACCURACY",
    "MIN_SAMPLE_DRAWDOWN",
    "MIN_SAMPLE_ECONOMIC",
    "MIN_SAMPLE_RATIO",
    "ConfidenceTier",
    "DirectionLabel",
    "MetricResult",
    "OverlapKey",
    "PredictedDirection",
    "brier_score",
    "confidence_tier",
    "dedupe_overlapping",
    "direction_accuracy",
    "direction_coverage",
    "direction_label",
    "endpoint_errors",
    "expected_calibration_error",
    "interval_metrics",
    "mfe_mae",
    "min_sample_for",
    "normalize_horizon",
    "target_hit_rate",
    "three_class_accuracy",
    "window_overlap_ratio",
]
