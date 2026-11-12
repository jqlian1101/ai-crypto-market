"""评估口径测试（PRD 第 33 章 / 36.3 必测清单）。

覆盖：

- 评估样本不足时不输出比率（33.6）
- 评估排名排除样本不足组（33.6.2）
- 评估重叠去重生效（33.7）
- 区间与覆盖率必须成对输出（33.5.2）
- ``confidence`` 不被当作概率（33.4）
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from aicrypto.domain.evaluation.metrics import (
    DIRECTION_THRESHOLD_PCT,
    ConfidenceTier,
    DirectionLabel,
    PredictedDirection,
    brier_score,
    confidence_tier,
    dedupe_overlapping,
    direction_accuracy,
    direction_coverage,
    direction_label,
    interval_metrics,
    mfe_mae,
    min_sample_for,
    normalize_horizon,
    target_hit_rate,
    window_overlap_ratio,
)

UTC = dt.timezone.utc


class TestDirectionLabel:
    """33.3.1：方向标签阈值 θ。"""

    def test_up_above_threshold(self) -> None:
        assert direction_label(0.5) is DirectionLabel.UP

    def test_down_below_threshold(self) -> None:
        assert direction_label(-0.5) is DirectionLabel.DOWN

    def test_flat_within_threshold(self) -> None:
        assert direction_label(0.1) is DirectionLabel.FLAT

    def test_threshold_boundary_is_flat(self) -> None:
        assert direction_label(DIRECTION_THRESHOLD_PCT) is DirectionLabel.FLAT
        assert direction_label(-DIRECTION_THRESHOLD_PCT) is DirectionLabel.FLAT


class TestSampleSize:
    """33.6：最小样本量。"""

    def test_ratio_metric_minimum_is_30(self) -> None:
        assert min_sample_for("SOME_RATIO") == 30

    def test_direction_accuracy_minimum_is_50(self) -> None:
        assert min_sample_for("DIRECTION_ACCURACY") == 50

    def test_brier_minimum_is_100(self) -> None:
        assert min_sample_for("BRIER_SCORE") == 100

    def test_grouped_doubles_the_threshold(self) -> None:
        assert min_sample_for("DIRECTION_ACCURACY", grouped=True) == 100

    def test_insufficient_sample_yields_no_ratio(self) -> None:
        """样本不足时 ``metric_value`` 为 NULL，只输出计数。"""
        samples = [
            (PredictedDirection.LONG, DirectionLabel.UP),
            (PredictedDirection.LONG, DirectionLabel.DOWN),
        ]
        result = direction_accuracy(samples)
        assert result.is_insufficient is True
        assert result.value is None
        assert result.sample_size == 2
        assert result.detail["correct"] == 1

    def test_low_confidence_band(self) -> None:
        """N_min ≤ N < 2×N_min → LOW_CONFIDENCE，禁止排名。"""
        tier = confidence_tier(60, "DIRECTION_ACCURACY")
        assert tier is ConfidenceTier.LOW_CONFIDENCE

    def test_normal_band(self) -> None:
        tier = confidence_tier(200, "DIRECTION_ACCURACY")
        assert tier is ConfidenceTier.NORMAL


class TestAccuracyCoveragePair:
    """33.3.2：Accuracy 与 Coverage 必须成对输出。"""

    def test_all_neutral_predictions_gives_zero_coverage(self) -> None:
        """退化情形：模型全输出 NEUTRAL → Coverage 极低，Accuracy 无意义。"""
        samples = [
            (PredictedDirection.NEUTRAL, DirectionLabel.UP),
            (PredictedDirection.NEUTRAL, DirectionLabel.DOWN),
        ]
        accuracy = direction_accuracy(samples)
        coverage = direction_coverage(samples)

        assert accuracy.sample_size == 0
        assert coverage.detail["binary_samples"] == 0
        assert coverage.detail["total_samples"] == 2

    def test_both_metrics_are_reported(self) -> None:
        samples = [
            (PredictedDirection.LONG, DirectionLabel.UP),
            (PredictedDirection.SHORT, DirectionLabel.DOWN),
        ]
        accuracy = direction_accuracy(samples)
        coverage = direction_coverage(samples)
        assert accuracy.metric_name == "DIRECTION_ACCURACY"
        assert coverage.metric_name == "DIRECTION_COVERAGE"
        assert coverage.sample_size == 2


class TestProbabilityVsConfidence:
    """33.4：``confidence`` 不被当作概率。"""

    def test_brier_requires_explicit_probability(self) -> None:
        result = brier_score([])
        assert result.value is None
        assert result.is_insufficient is True

    def test_brier_computed_with_probability(self) -> None:
        samples = [
            (0.9, DirectionLabel.UP),
            (0.1, DirectionLabel.DOWN),
        ]
        result = brier_score(samples)
        # 样本不足 100 → 不输出比率，但 detail 保留均值
        assert result.is_insufficient is True
        assert result.detail["mean_probability"] == pytest.approx(0.5)


class TestIntervalPair:
    """33.5.2：区间与覆盖率必须成对输出。"""

    def test_coverage_and_width_returned_together(self) -> None:
        samples = [
            (Decimal("49000"), Decimal("51000"), Decimal("50000")),
            (Decimal("49000"), Decimal("51000"), Decimal("52000")),
        ]
        coverage, width = interval_metrics(samples, base_price=Decimal("50000"))
        assert coverage.metric_name == "INTERVAL_COVERAGE"
        assert width.metric_name == "INTERVAL_WIDTH"
        assert coverage.detail["avg_width_ratio"] == pytest.approx(0.04)
        assert width.detail["coverage"] == pytest.approx(0.5)


class TestMfeMae:
    """33.5.3：路径指标用 high/low，含影线。"""

    def test_long_mfe_mae(self) -> None:
        candles = [(Decimal("110"), Decimal("95")), (Decimal("108"), Decimal("99"))]
        mfe, mae = mfe_mae(
            PredictedDirection.LONG, base_price=Decimal("100"), candles=candles
        )
        assert mfe == pytest.approx(0.10)
        assert mae == pytest.approx(0.05)

    def test_short_mfe_mae(self) -> None:
        candles = [(Decimal("110"), Decimal("95"))]
        mfe, mae = mfe_mae(
            PredictedDirection.SHORT, base_price=Decimal("100"), candles=candles
        )
        assert mfe == pytest.approx(0.05)
        assert mae == pytest.approx(0.10)

    def test_neutral_is_not_applicable(self) -> None:
        candles = [(Decimal("110"), Decimal("95"))]
        assert mfe_mae(
            PredictedDirection.NEUTRAL, base_price=Decimal("100"), candles=candles
        ) == (None, None)


class TestTargetHit:
    """33.5.4：盘中触达即算。"""

    def test_target_touched_intrabar(self) -> None:
        candles = [(Decimal("120"), Decimal("99"))]
        hit, stop = target_hit_rate(
            PredictedDirection.LONG,
            targets=[Decimal("115")],
            stop_loss=Decimal("95"),
            candles=candles,
        )
        assert hit == 1.0
        assert stop == 0.0

    def test_stop_touched_intrabar(self) -> None:
        candles = [(Decimal("105"), Decimal("90"))]
        hit, stop = target_hit_rate(
            PredictedDirection.LONG,
            targets=[Decimal("115")],
            stop_loss=Decimal("95"),
            candles=candles,
        )
        assert hit == 0.0
        assert stop == 1.0


class TestHorizonNormalization:
    """33.1：窗口向上取整到 15m 边界。"""

    def test_exact_multiple_unchanged(self) -> None:
        assert normalize_horizon(4 * 3600) == 4 * 3600

    def test_rounds_up(self) -> None:
        assert normalize_horizon(901) == 1800

    def test_rejects_non_positive(self) -> None:
        with pytest.raises(ValueError):
            normalize_horizon(0)


class TestOverlapDedup:
    """33.7：重叠预测窗口去重。"""

    def test_overlap_ratio(self) -> None:
        a = (dt.datetime(2026, 11, 12, 0, tzinfo=UTC), dt.datetime(2026, 11, 12, 1, tzinfo=UTC))
        b = (dt.datetime(2026, 11, 12, 0, 30, tzinfo=UTC), dt.datetime(2026, 11, 12, 1, 30, tzinfo=UTC))
        assert window_overlap_ratio(a, b) == pytest.approx(0.5)

    def test_dedupe_keeps_earliest(self) -> None:
        from aicrypto.domain.evaluation.metrics import OverlapKey

        key = OverlapKey("BTCUSDT", "1h", "1h", "LONG")
        t0 = dt.datetime(2026, 11, 12, 0, tzinfo=UTC)
        samples = [
            (key, t0, (t0, t0 + dt.timedelta(hours=1))),
            (key, t0 + dt.timedelta(minutes=30), (t0 + dt.timedelta(minutes=30), t0 + dt.timedelta(minutes=90))),
        ]
        kept, excluded = dedupe_overlapping(samples)
        assert kept == [0]
        assert excluded == [1]

    def test_non_overlapping_are_all_kept(self) -> None:
        from aicrypto.domain.evaluation.metrics import OverlapKey

        key = OverlapKey("BTCUSDT", "1h", "1h", "LONG")
        t0 = dt.datetime(2026, 11, 12, 0, tzinfo=UTC)
        samples = [
            (key, t0, (t0, t0 + dt.timedelta(hours=1))),
            (key, t0 + dt.timedelta(hours=5), (t0 + dt.timedelta(hours=5), t0 + dt.timedelta(hours=6))),
        ]
        kept, excluded = dedupe_overlapping(samples)
        assert kept == [0, 1]
        assert excluded == []
