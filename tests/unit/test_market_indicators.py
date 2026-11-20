"""纯 K 线指标与结构测试（PRD 4.5 / 4.6 / 38.2.1）。"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from aicrypto.domain.market.indicators import (
    FEATURE_VERSION,
    atr,
    bollinger,
    ema,
    ema_series,
    macd,
    rsi,
    sma,
    true_ranges,
    volume_stats,
)
from aicrypto.domain.market.structure import (
    MarketRegime,
    StructureBias,
    classify_regime,
    sr_zones,
    summarize_structure,
    swing_points,
)


def D(values: list[float]) -> list[Decimal]:
    return [Decimal(str(v)) for v in values]


class TestSma:
    def test_basic(self) -> None:
        assert sma(D([1, 2, 3, 4, 5]), 3) == pytest.approx(4.0)

    def test_insufficient_returns_none(self) -> None:
        assert sma(D([1, 2]), 3) is None

    def test_invalid_period(self) -> None:
        with pytest.raises(ValueError):
            sma(D([1]), 0)


class TestEma:
    def test_series_warmup_and_seed(self) -> None:
        series = ema_series([1.0, 2.0, 3.0, 4.0, 5.0], 3)
        assert series[:2] == [None, None]
        assert series[2] == pytest.approx(2.0)  # 种子 = SMA(1,2,3)
        alpha = 2 / 4
        assert series[3] == pytest.approx(alpha * 4 + (1 - alpha) * 2.0)

    def test_constant_input_is_constant(self) -> None:
        assert ema(D([10] * 30), 12) == pytest.approx(10.0)

    def test_insufficient_returns_none(self) -> None:
        assert ema(D([1, 2]), 5) is None


class TestRsi:
    def test_monotonic_rise_is_100(self) -> None:
        assert rsi(D(list(range(1, 20))), 14) == pytest.approx(100.0)

    def test_monotonic_fall_is_0(self) -> None:
        assert rsi(D(list(range(20, 1, -1))), 14) == pytest.approx(0.0)

    def test_flat_is_50(self) -> None:
        assert rsi(D([5] * 20), 14) == pytest.approx(50.0)

    def test_warmup_requires_period_plus_one(self) -> None:
        assert rsi(D(list(range(1, 15))), 14) is None  # 14 根，不足 15
        assert rsi(D(list(range(1, 16))), 14) is not None

    def test_alternating_is_mid_range(self) -> None:
        values = [10 + (i % 2) for i in range(40)]
        value = rsi(D(values), 14)
        assert value is not None
        assert 40 < value < 60


class TestMacd:
    def test_constant_is_zero(self) -> None:
        result = macd(D([100] * 60))
        assert result is not None
        assert result.macd == pytest.approx(0.0, abs=1e-9)
        assert result.histogram == pytest.approx(0.0, abs=1e-9)

    def test_uptrend_positive_macd(self) -> None:
        result = macd(D([100 + i for i in range(60)]))
        assert result is not None
        assert result.macd > 0

    def test_warmup_insufficient(self) -> None:
        assert macd(D([1.0] * 30)) is None

    def test_fast_must_be_less_than_slow(self) -> None:
        with pytest.raises(ValueError):
            macd(D([1.0] * 50), fast=26, slow=12)


class TestAtr:
    def test_true_range_first_bar_uses_own_range(self) -> None:
        trs = true_ranges(D([10, 12]), D([8, 9]), D([9, 11]))
        assert trs[0] == pytest.approx(2.0)
        # 第二根：max(12-9, |12-9|, |9-9|) = 3
        assert trs[1] == pytest.approx(3.0)

    def test_constant_range(self) -> None:
        highs = D([11] * 30)
        lows = D([9] * 30)
        closes = D([10] * 30)
        assert atr(highs, lows, closes, 14) == pytest.approx(2.0)

    def test_insufficient(self) -> None:
        assert atr(D([1]), D([0]), D([0]), 14) is None

    def test_length_mismatch(self) -> None:
        with pytest.raises(ValueError):
            true_ranges(D([1, 2]), D([1]), D([1, 2]))


class TestBollinger:
    def test_constant_band_collapses(self) -> None:
        result = bollinger(D([10] * 20), 20, 2.0)
        assert result is not None
        assert result.upper == pytest.approx(10.0)
        assert result.lower == pytest.approx(10.0)
        assert result.percent_b is None
        assert result.bandwidth == pytest.approx(0.0)

    def test_population_std(self) -> None:
        values = D([1, 2, 3, 4])
        result = bollinger(values, 4, 1.0)
        assert result is not None
        std = math.sqrt(((1 - 2.5) ** 2 + (2 - 2.5) ** 2 + (3 - 2.5) ** 2 + (4 - 2.5) ** 2) / 4)
        assert result.upper == pytest.approx(2.5 + std)
        assert result.middle == pytest.approx(2.5)


class TestVolumeStats:
    def test_ratio_and_percentile(self) -> None:
        stats = volume_stats(D([1, 1, 1, 1, 4]), 5)
        assert stats is not None
        assert stats["mean"] == pytest.approx(1.6)
        assert stats["ratio_to_mean"] == pytest.approx(4 / 1.6)
        assert stats["percentile"] == pytest.approx(1.0)

    def test_zero_mean_ratio_is_none(self) -> None:
        stats = volume_stats(D([0, 0, 0]), 3)
        assert stats is not None
        assert stats["ratio_to_mean"] is None


def test_feature_version_is_declared() -> None:
    assert FEATURE_VERSION.startswith("indicators-")


class TestSwingAndStructure:
    def test_swing_high_detected(self) -> None:
        highs = D([1, 2, 5, 2, 1, 1, 1])
        lows = D([0, 1, 4, 1, 0, 0, 0])
        points = swing_points(highs, lows, left=2, right=2)
        assert any(p.kind == "HIGH" and p.index == 2 for p in points)

    def test_trailing_bars_not_confirmed(self) -> None:
        # 末尾峰值右侧不足 right 根，不得计入（防未来数据）
        highs = D([1, 2, 3, 4, 9])
        lows = D([0, 1, 2, 3, 8])
        points = swing_points(highs, lows, left=2, right=2)
        assert all(p.index != 4 for p in points)

    def test_uptrend_bias(self) -> None:
        # 两个递增的摆动高点与低点
        highs = D([5, 6, 9, 6, 5, 6, 7, 10, 7, 6, 6, 6])
        lows = D([4, 5, 7, 5, 4, 5, 6, 8, 6, 5, 6, 6])
        summary = summarize_structure(highs, lows, left=2, right=2)
        assert summary.bias == StructureBias.UPTREND
        assert summary.higher_highs is True
        assert summary.higher_lows is True

    def test_insufficient_structure(self) -> None:
        summary = summarize_structure(D([1, 2, 3]), D([0, 1, 2]))
        assert summary.bias == StructureBias.INSUFFICIENT

    def test_sr_zone_clusters_close_swings(self) -> None:
        highs = D([10, 12, 10.01, 9, 8, 9, 10.005, 9, 8, 9])
        lows = D([9, 11, 9.5, 8, 7, 8, 9.5, 8, 7, 8])
        closes = D([9] * 10)
        zones = sr_zones(highs, lows, closes, left=1, right=1, tolerance_ratio=0.01)
        assert zones
        assert all(z.touches >= 2 for z in zones)


class TestRegime:
    def test_insufficient_when_short(self) -> None:
        values = D([100 + i * 0.1 for i in range(60)])
        result = classify_regime(values, values, values)
        assert result.regime == MarketRegime.INSUFFICIENT

    def test_uptrend(self) -> None:
        n = 200
        closes = D([100 + i * 0.5 for i in range(n)])
        highs = [c + Decimal("0.2") for c in closes]
        lows = [c - Decimal("0.2") for c in closes]
        result = classify_regime(highs, lows, closes)
        assert result.regime == MarketRegime.TREND_UP

    def test_downtrend(self) -> None:
        n = 200
        closes = D([200 - i * 0.5 for i in range(n)])
        highs = [c + Decimal("0.2") for c in closes]
        lows = [c - Decimal("0.2") for c in closes]
        result = classify_regime(highs, lows, closes)
        assert result.regime == MarketRegime.TREND_DOWN

    def test_steady_downtrend_is_not_high_volatility(self) -> None:
        # 回归：下跌使 ATR/收盘价抬升，不得因此判为高波动
        n = 300
        closes = D([300 - i * 0.5 for i in range(n)])
        highs = [c + Decimal("0.2") for c in closes]
        lows = [c - Decimal("0.2") for c in closes]
        result = classify_regime(highs, lows, closes)
        assert result.regime != MarketRegime.HIGH_VOLATILITY

    def test_high_volatility_spike(self) -> None:
        n = 200
        closes = [Decimal("100")] * n
        highs = [Decimal("100.1")] * n
        lows = [Decimal("99.9")] * n
        # 末根振幅突然放大
        highs[-1] = Decimal("130")
        lows[-1] = Decimal("70")
        result = classify_regime(highs, lows, closes)
        assert result.regime == MarketRegime.HIGH_VOLATILITY
