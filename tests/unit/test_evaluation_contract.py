"""评估口径定稿测试（PRD 33.2 / 33.3.1 / 33.8）。

锁定关键口径，防止后续改动无意改变历史结果的可比性：

- θ = 0.30%，边界值归为 FLAT（严格不等号）；
- 基准价只取 t0 之前已收盘的 K 线 close，不得使用未来数据；
- evaluator_version 与评估粒度固定。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from aicrypto.domain.evaluation.metrics import (
    DIRECTION_THRESHOLD_PCT,
    EVALUATION_GRANULARITY,
    EVALUATOR_VERSION,
    DirectionLabel,
    PredictedDirection,
    base_price_from_candles,
    direction_label,
    normalize_horizon,
)

UTC = dt.UTC
T0 = dt.datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def test_locked_constants() -> None:
    assert DIRECTION_THRESHOLD_PCT == 0.30
    assert EVALUATION_GRANULARITY == "15m"
    assert EVALUATOR_VERSION == "v1.0.0"


def test_threshold_boundary_is_flat() -> None:
    assert direction_label(0.30) == DirectionLabel.FLAT
    assert direction_label(-0.30) == DirectionLabel.FLAT


def test_just_beyond_threshold_is_directional() -> None:
    assert direction_label(0.31) == DirectionLabel.UP
    assert direction_label(-0.31) == DirectionLabel.DOWN


def test_base_price_uses_last_closed_before_t0() -> None:
    closes = [
        (T0 - dt.timedelta(minutes=30), Decimal("100")),
        (T0 - dt.timedelta(minutes=15), Decimal("101")),
        (T0, Decimal("102")),  # close_time == t0 允许
        (T0 + dt.timedelta(minutes=15), Decimal("999")),  # 未来数据，必须忽略
    ]
    assert base_price_from_candles(closes, t0=T0) == Decimal("102")


def test_base_price_none_when_no_history() -> None:
    closes = [(T0 + dt.timedelta(minutes=15), Decimal("999"))]
    assert base_price_from_candles(closes, t0=T0) is None


def test_base_price_order_independent() -> None:
    closes = [
        (T0 - dt.timedelta(minutes=15), Decimal("101")),
        (T0 - dt.timedelta(minutes=30), Decimal("100")),
    ]
    assert base_price_from_candles(closes, t0=T0) == Decimal("101")


def test_horizon_rounds_to_15m_grid() -> None:
    assert normalize_horizon(900) == 900
    assert normalize_horizon(901) == 1800
    assert normalize_horizon(3600) == 3600


def test_predicted_direction_values_are_stable() -> None:
    assert {p.value for p in PredictedDirection} == {"LONG", "SHORT", "NEUTRAL"}
