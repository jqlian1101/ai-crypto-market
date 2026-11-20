"""config/base.toml 与 PRD 22.2 初始阈值的一致性（防止再次漂移）。"""

from __future__ import annotations

import tomllib
from pathlib import Path

from aicrypto.domain.anomaly.rules import (
    PriceMoveThresholds,
    VolatilityThresholds,
    VolumeThresholds,
)
from aicrypto.domain.evaluation import metrics

CONFIG = Path(__file__).resolve().parents[2] / "config" / "base.toml"


def _load() -> dict:
    with CONFIG.open("rb") as fh:
        return tomllib.load(fh)


def test_price_move_matches_prd() -> None:
    rules = _load()["anomaly"]["rules"]["price_move"]
    defaults = PriceMoveThresholds()
    for tf in ("5m", "15m", "1h"):
        assert rules[f"{tf}_warn_pct"] == defaults.warn_pct[tf]
        assert rules[f"{tf}_critical_pct"] == defaults.critical_pct[tf]


def test_volatility_matches_prd() -> None:
    rules = _load()["anomaly"]["rules"]["volatility"]
    defaults = VolatilityThresholds()
    assert rules["atr_ratio_warn"] == defaults.warn_ratio
    assert rules["atr_ratio_critical"] == defaults.critical_ratio
    assert rules["atr_baseline_bars"] == defaults.baseline_bars


def test_volume_matches_prd() -> None:
    rules = _load()["anomaly"]["rules"]["volume"]
    defaults = VolumeThresholds()
    assert rules["volume_ratio_warn"] == defaults.warn_ratio
    assert rules["volume_ratio_critical"] == defaults.critical_ratio
    assert rules["volume_lookback"] == defaults.baseline_bars


def test_1m_rules_explicitly_disabled() -> None:
    disabled = set(_load()["anomaly"]["rules"]["disabled_rules"])
    assert {"PRICE_PCT_1M", "PRICE_PCT_1M_CRIT", "PRICE_GAP", "PRICE_FLASH_MOVE"} <= disabled


def test_rule_version_declared() -> None:
    assert _load()["anomaly"]["rules"]["rule_version"] == "v1"


def test_min_sample_sizes_match_metrics() -> None:
    assert metrics.min_sample_for("DIRECTION_ACCURACY") == 50
    assert metrics.min_sample_for("BRIER_SCORE") == 100
    assert metrics.min_sample_for("OTHER") == 30
    assert "min_sample_size" not in _load()["evaluation"]
