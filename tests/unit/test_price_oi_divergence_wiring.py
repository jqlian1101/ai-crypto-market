"""价格/OI 背离与多空偏向的接线测试（PRD 22.2.4 / L-06）。

**背景**：领域层早就实现了 ``price_oi_divergence()`` 与 ``long_short_skew()``，
但快照层调用时把 ``price_change_pct`` **恒传 None**，
并把 ``detected`` 写成了**字面量 False**——于是：

- ``price_oi_divergence`` 全仓无调用点；
- ``long_short_skew`` 传 None 时返回 ``NEUTRAL``，**不报错**，
  属于**静默失效**：快照看起来正常，但信号恒为中性。

这些测试锁死两个不变量：

1. ``detected`` 必须来自领域层真实计算，不得是字面量；
2. 价格变化率缺失时必须显式表达「无法计算」，不得用 0 冒充。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from aicrypto.domain.market.derivatives import (
    long_short_skew,
    price_oi_divergence,
)
from aicrypto.services.derivatives_snapshot import (
    OI_CHANGE_WINDOW_SECONDS,
    PRICE_CHANGE_TIMEFRAME,
)

UTC = dt.UTC
BASE = dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# 领域层本身的语义（作为接线测试的基准）
# --------------------------------------------------------------------------


def test_divergence_price_up_oi_down() -> None:
    """价格上涨但持仓量下降 → 疑似平仓推动。"""
    assert price_oi_divergence(0.02, -0.02) is True


def test_divergence_price_down_oi_up() -> None:
    """价格下跌但持仓量上升 → 疑似新空进场。"""
    assert price_oi_divergence(-0.02, 0.02) is True


def test_divergence_same_direction_is_false() -> None:
    """同向变动不是背离。"""
    assert price_oi_divergence(0.02, 0.02) is False
    assert price_oi_divergence(-0.02, -0.02) is False


def test_divergence_below_threshold_is_false() -> None:
    """反向变动但幅度未超阈值（默认 1%）→ 不算背离。"""
    assert price_oi_divergence(0.005, -0.005) is False


def test_divergence_missing_input_is_false() -> None:
    """缺任一输入时返回 False，不猜测。"""
    assert price_oi_divergence(None, 0.02) is False
    assert price_oi_divergence(0.02, None) is False


def test_skew_requires_funding_rate() -> None:
    """没有资金费率时无法判断拥挤度 → UNKNOWN。"""
    assert long_short_skew(oi_change=0.05, funding_rate=None, price_change_pct=0.03) == "UNKNOWN"


def test_skew_long_crowded_by_funding_and_oi() -> None:
    assert long_short_skew(oi_change=0.05, funding_rate=0.001, price_change_pct=None) == (
        "LONG_CROWDED"
    )


def test_skew_short_crowded_by_funding_and_oi() -> None:
    assert long_short_skew(oi_change=0.05, funding_rate=-0.001, price_change_pct=None) == (
        "SHORT_CROWDED"
    )


def test_skew_uses_price_change_when_funding_neutral() -> None:
    """资金费率中性时，靠价格方向也能给出倾向。"""
    assert (
        long_short_skew(oi_change=0.0, funding_rate=0.0001, price_change_pct=0.05) == "LONG_CROWDED"
    )
    assert (
        long_short_skew(oi_change=0.0, funding_rate=-0.0001, price_change_pct=-0.05)
        == "SHORT_CROWDED"
    )


def test_skew_neutral_when_nothing_points_a_way() -> None:
    assert long_short_skew(oi_change=0.0, funding_rate=0.0001, price_change_pct=0.0) == "NEUTRAL"


# --------------------------------------------------------------------------
# 接线：快照必须真的算出价格变化率
# --------------------------------------------------------------------------


def test_price_change_timeframe_matches_oi_window() -> None:
    """价格与 OI 必须同窗口——否则是量纲不同的两个区间在比较。"""
    assert OI_CHANGE_WINDOW_SECONDS == 3600
    assert PRICE_CHANGE_TIMEFRAME == "1h"


def test_snapshot_module_imports_price_oi_divergence() -> None:
    """快照模块必须真的调用领域层的判定，而不是手写字面量。

    用源码检查锁死：``detected: False`` 这种写法一旦回归就会被抓住。
    """
    import inspect

    import aicrypto.services.derivatives_snapshot as mod

    source = inspect.getsource(mod)
    # 不得出现手写字面量
    assert '"detected": False' not in source
    assert "'detected': False" not in source
    # 必须调用领域层函数
    assert "price_oi_divergence(" in source


def test_snapshot_passes_real_price_change_to_skew() -> None:
    """``long_short_skew`` 必须收到真实价格变化率，而不是恒 None。"""
    import inspect

    import aicrypto.services.derivatives_snapshot as mod

    source = inspect.getsource(mod)
    assert "price_change_pct=None" not in source
    assert "price_change_pct=price_change" in source


def test_snapshot_uses_price_change_pct_loader() -> None:
    """快照必须真的去取价格变化率。"""
    import inspect

    import aicrypto.services.derivatives_snapshot as mod

    source = inspect.getsource(mod)
    assert "price_change_pct(" in source
    assert "as_of=now" in source


# --------------------------------------------------------------------------
# price_change_pct 的计算口径
# --------------------------------------------------------------------------


class _FakeCandle:
    def __init__(self, close: str, close_time: dt.datetime) -> None:
        self.close = Decimal(close)
        self.close_time = close_time
        self.open_time = close_time - dt.timedelta(hours=1)


class _FakeResult:
    def __init__(self, rows: list[_FakeCandle]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[_FakeCandle]:
        return self._rows

    def first(self) -> _FakeCandle | None:
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    """按过滤条件返回预置 K 线。

    查询是「按 close_time 倒序、取 limit 根」，这里如实模拟：
    ``limit`` 会被正确截断——否则测不出「取够根数」这个关键行为。
    """

    def __init__(self, rows: list[_FakeCandle]) -> None:
        self._rows = rows
        self._limit: int | None = None

    def execute(self, stmt: object) -> _FakeResult:
        ordered = sorted(self._rows, key=lambda r: r.close_time, reverse=True)
        limit = _extract_limit(stmt)
        if limit is not None:
            ordered = ordered[:limit]
        return _FakeResult(ordered)


def _extract_limit(stmt: object) -> int | None:
    """从 select 语句里取 limit 值（测试桩用）。"""
    limit = getattr(stmt, "_limit", None)
    return None if limit is None else int(limit)


@pytest.mark.parametrize(
    ("closes", "expected"),
    [
        (["100", "110"], 10.0),
        (["100", "90"], -10.0),
        (["100", "100"], 0.0),
    ],
)
def test_price_change_pct_computes_from_first_to_last(closes: list[str], expected: float) -> None:
    """窗口内最老那根与最新那根的收盘价比较。"""
    from aicrypto.services.market import price_change_pct

    rows = [_FakeCandle(c, BASE + dt.timedelta(hours=i)) for i, c in enumerate(closes)]
    change = price_change_pct(
        _FakeSession(rows),  # type: ignore[arg-type]
        symbol="BTCUSDT",
        market_type="USDM_FUTURES",
        timeframe="1h",
        window_seconds=3600,
        as_of=BASE + dt.timedelta(hours=len(closes)),
    )
    assert change == pytest.approx(expected)


def test_price_change_pct_returns_none_when_insufficient_data() -> None:
    """只有一根 K 线时无法比较 → 返回 None，不返回 0。"""
    from aicrypto.services.market import price_change_pct

    rows = [_FakeCandle("100", BASE)]
    change = price_change_pct(
        _FakeSession(rows),  # type: ignore[arg-type]
        symbol="BTCUSDT",
        market_type="USDM_FUTURES",
        timeframe="1h",
        window_seconds=3600,
        as_of=BASE + dt.timedelta(hours=1),
    )
    assert change is None


def test_price_change_pct_returns_none_when_no_data() -> None:
    """完全没有 K 线 → None。"""
    from aicrypto.services.market import price_change_pct

    change = price_change_pct(
        _FakeSession([]),  # type: ignore[arg-type]
        symbol="BTCUSDT",
        market_type="USDM_FUTURES",
        timeframe="1h",
        window_seconds=3600,
        as_of=BASE,
    )
    assert change is None


def test_price_change_pct_returns_none_when_base_is_zero() -> None:
    """基准价为 0 时不能做除法 → None。"""
    from aicrypto.services.market import price_change_pct

    rows = [
        _FakeCandle("0", BASE),
        _FakeCandle("110", BASE + dt.timedelta(hours=1)),
    ]
    change = price_change_pct(
        _FakeSession(rows),  # type: ignore[arg-type]
        symbol="BTCUSDT",
        market_type="USDM_FUTURES",
        timeframe="1h",
        window_seconds=3600,
        as_of=BASE + dt.timedelta(hours=2),
    )
    assert change is None


def test_price_change_pct_takes_enough_candles_to_cover_window() -> None:
    """必须取够覆盖整个窗口的根数，而不是「最后一根 vs 倒数第二根」。

    1h 周期、1h 窗口 → 需要 2 根，覆盖 1 小时；
    1h 周期、2h 窗口 → 需要 3 根，覆盖 2 小时。
    """
    from aicrypto.services.market import price_change_pct

    rows = [
        _FakeCandle("100", BASE),
        _FakeCandle("110", BASE + dt.timedelta(hours=1)),
        _FakeCandle("120", BASE + dt.timedelta(hours=2)),
    ]
    session = _FakeSession(rows)
    # 窗口 1h：最新(120) vs 1h 前那根(110) → +9.09%
    one_hour = price_change_pct(
        session,  # type: ignore[arg-type]
        symbol="BTCUSDT",
        market_type="USDM_FUTURES",
        timeframe="1h",
        window_seconds=3600,
        as_of=BASE + dt.timedelta(hours=3),
    )
    assert one_hour == pytest.approx((120 - 110) / 110 * 100)
    # 窗口 2h：最新(120) vs 2h 前那根(100) → +20%
    two_hours = price_change_pct(
        session,  # type: ignore[arg-type]
        symbol="BTCUSDT",
        market_type="USDM_FUTURES",
        timeframe="1h",
        window_seconds=7200,
        as_of=BASE + dt.timedelta(hours=3),
    )
    assert two_hours == pytest.approx(20.0)


def test_price_change_pct_uses_only_closed_candles() -> None:
    """未收盘 K 线不得参与（29.5）。

    源码层面锁死：查询必须带 ``is_closed`` 过滤。
    """
    import inspect

    from aicrypto.services.market import price_change_pct

    source = inspect.getsource(price_change_pct)
    assert "_closed_filters" in source
    # _closed_filters 内部带 is_closed 与 close_time <= as_of
    from aicrypto.services.market import _closed_filters

    filters = _closed_filters("BTCUSDT", "USDM_FUTURES", "1h", BASE)
    # 至少包含 is_closed 与 close_time 两个条件
    assert len(filters) >= 5


def test_price_change_pct_rejects_invalid_timeframe() -> None:
    """非法周期必须报错，不能静默降级。"""
    from aicrypto.common.errors import AICryptoError
    from aicrypto.services.market import price_change_pct

    with pytest.raises(AICryptoError):
        price_change_pct(
            _FakeSession([]),  # type: ignore[arg-type]
            symbol="BTCUSDT",
            market_type="USDM_FUTURES",
            timeframe="7m",
            window_seconds=3600,
            as_of=BASE,
        )


def test_price_change_pct_rejects_invalid_market_type() -> None:
    from aicrypto.common.errors import AICryptoError
    from aicrypto.services.market import price_change_pct

    with pytest.raises(AICryptoError):
        price_change_pct(
            _FakeSession([]),  # type: ignore[arg-type]
            symbol="BTCUSDT",
            market_type="DUMMY",
            timeframe="1h",
            window_seconds=3600,
            as_of=BASE,
        )
