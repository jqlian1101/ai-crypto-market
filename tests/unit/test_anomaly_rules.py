"""异动规则纯函数测试（PRD 22.2 / 36.5 回放的单元层）。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aicrypto.domain.anomaly import (
    atr_series,
    evaluate_price_move,
    evaluate_volatility,
    evaluate_volume,
)
from aicrypto.domain.anomaly.rules import VolatilityThresholds, VolumeThresholds


def D(v: float | str) -> Decimal:
    return Decimal(str(v))


class TestPriceMove:
    def test_warn_at_exact_threshold(self) -> None:
        # 5m WARN = 0.60%，恰好等于阈值应触发（>=）
        hit = evaluate_price_move(timeframe="5m", window_open=D(100), window_close=D("100.6"))
        assert hit is not None
        assert hit.rule_id == "PRICE_PCT_5M"
        assert hit.severity == "WARN"
        assert hit.direction == "UP"

    def test_critical_downward(self) -> None:
        hit = evaluate_price_move(timeframe="5m", window_open=D(100), window_close=D("98.7"))
        assert hit is not None
        assert hit.rule_id == "PRICE_PCT_5M_CRIT"
        assert hit.severity == "CRITICAL"
        assert hit.direction == "DOWN"
        assert hit.threshold["price_change_pct"] == pytest.approx(1.20)

    def test_below_warn_returns_none(self) -> None:
        assert (
            evaluate_price_move(timeframe="15m", window_open=D(100), window_close=D("101.19"))
            is None
        )

    def test_1h_critical(self) -> None:
        hit = evaluate_price_move(timeframe="1h", window_open=D(100), window_close=D("95.4"))
        assert hit is not None
        assert hit.rule_id == "PRICE_PCT_1H_CRIT"

    def test_1m_rejected_explicitly(self) -> None:
        # 1m 依赖规则须显式禁用，不得静默返回 None
        with pytest.raises(ValueError, match="1m"):
            evaluate_price_move(timeframe="1m", window_open=D(100), window_close=D(101))

    def test_zero_open_returns_none(self) -> None:
        assert evaluate_price_move(timeframe="5m", window_open=D(0), window_close=D(1)) is None


def _flat_bars(count: int, range_size: float) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
    closes = [D(100)] * count
    highs = [D(100 + range_size / 2)] * count
    lows = [D(100 - range_size / 2)] * count
    return highs, lows, closes


class TestAtrSeries:
    def test_warmup_is_none(self) -> None:
        highs, lows, closes = _flat_bars(20, 2.0)
        series = atr_series(highs, lows, closes, 14)
        assert all(v is None for v in series[:13])
        assert series[13] == pytest.approx(2.0)

    def test_uses_only_past_bars(self) -> None:
        # 截断后序列的前缀不应改变（无未来依赖）
        highs, lows, closes = _flat_bars(40, 2.0)
        highs[35] = D(150)
        full = atr_series(highs, lows, closes, 14)
        prefix = atr_series(highs[:30], lows[:30], closes[:30], 14)
        assert full[:30] == prefix


class TestVolatilitySpike:
    def test_spike_triggers_warn(self) -> None:
        thresholds = VolatilityThresholds(baseline_bars=20, warn_ratio=1.8, critical_ratio=2.5)
        highs, lows, closes = _flat_bars(60, 2.0)
        # 最后一根真实波幅 = 40（基线 2）。ATR 约增至 (13*2+40)/14 ≈ 4.7，比值 ≈ 2.3，
        # 落在 WARN(1.8) 与 CRITICAL(2.5) 之间
        highs[-1] = D(120)
        lows[-1] = D(80)
        hit = evaluate_volatility(highs, lows, closes, thresholds=thresholds)
        assert hit is not None
        assert hit.severity == "WARN"
        assert hit.rule_id == "VOL_ATR_SPIKE"
        assert hit.metrics["atr_ratio"] == pytest.approx(2.3, abs=0.1)

    def test_flat_market_no_hit(self) -> None:
        thresholds = VolatilityThresholds(baseline_bars=20)
        highs, lows, closes = _flat_bars(60, 2.0)
        assert evaluate_volatility(highs, lows, closes, thresholds=thresholds) is None

    def test_insufficient_history_returns_none(self) -> None:
        highs, lows, closes = _flat_bars(20, 2.0)
        assert evaluate_volatility(highs, lows, closes) is None


class TestVolume:
    def test_critical_relative_volume(self) -> None:
        volumes = [D(10)] * 20 + [D(70)]
        closes = [D(100)] * 21
        opens = [D(99)] * 21
        hit = evaluate_volume(volumes, closes, opens, thresholds=VolumeThresholds(baseline_bars=20))
        assert hit is not None
        assert hit.rule_id == "VOL_REL_VOLUME_CRIT"
        assert hit.direction == "UP"
        assert hit.metrics["relative_volume"] == pytest.approx(7.0)

    def test_warn_direction_follows_last_bar(self) -> None:
        volumes = [D(10)] * 20 + [D(35)]
        closes = [D(98)] * 21
        opens = [D(100)] * 21
        hit = evaluate_volume(volumes, closes, opens, thresholds=VolumeThresholds(baseline_bars=20))
        assert hit is not None
        assert hit.severity == "WARN"
        assert hit.direction == "DOWN"

    def test_baseline_excludes_current_bar(self) -> None:
        # 当前根若计入基线会稀释倍数，应仍判为 CRITICAL
        volumes = [D(10)] * 20 + [D(100)]
        closes = [D(100)] * 21
        opens = [D(100)] * 21
        hit = evaluate_volume(volumes, closes, opens, thresholds=VolumeThresholds(baseline_bars=20))
        assert hit is not None
        assert hit.metrics["relative_volume"] == pytest.approx(10.0)

    def test_zero_baseline_returns_none(self) -> None:
        volumes = [D(0)] * 20 + [D(5)]
        closes = [D(100)] * 21
        opens = [D(100)] * 21
        assert evaluate_volume(volumes, closes, opens) is None
