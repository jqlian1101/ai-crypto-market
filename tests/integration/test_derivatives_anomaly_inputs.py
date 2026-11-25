"""衍生品取数层的回归测试（锁死 3 个真实缺陷）。

这三个 bug 都是**单元测试测不出来**的——它们只在真实数据的时间序列上
才暴露，且都表现为「规则永远不触发」而非「报错」：

1. **基差分位取错样本**（``_basis_percentile``）
   样本按 ``captured_at`` **降序**排列，``samples[-1]`` 是**最旧**的样本。
   原本用它当「当前值」，导致分位算在历史值上，
   ``DER_BASIS_EXTREME`` 永远不触发。

2. **多空分位的历史样本套用当前 OI 变化**
   所有历史样本共用同一个 OI 变化率，标量全部相同，
   分位恒为 0.5，``DER_LS_SKEW`` 永远不触发。

3. **OI 变化率单位不匹配**（``_skew_scalar``）
   ``oi_change_pct`` 返回**小数**（0.3 = +30%），
   阈值却是**百分数**（1.0 = 1%），导致 0.3 < 1.0 恒成立，
   多空拥挤永远无法被确认。
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from aicrypto.services.anomaly_scan import (
    SCAN_MARKET_TYPE,
    _skew_scalar,
    ensure_rule_version,
    load_derivatives_inputs,
    percentile_of,
    scan_symbol,
)

UTC = dt.UTC
NOW = dt.datetime.now(UTC).replace(microsecond=0)
SYMBOL = "P3REGRESS"


@pytest.fixture
def db() -> Session:
    """事务级隔离：测试结束后整体回滚，不污染数据库。"""
    url = os.getenv("ANOMALY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 ANOMALY_TEST_DATABASE_URL 以执行衍生品取数集成测试")
    engine = create_engine(url)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _add_book(db: Session, minutes: int, spot_mid: Decimal) -> None:
    """写入同一时刻的 Futures + SPOT 双侧盘口。"""
    from aicrypto.infrastructure.database.models.market import OrderbookSnapshot

    cap = NOW - dt.timedelta(minutes=minutes)
    db.add(
        OrderbookSnapshot(
            exchange="binance",
            market_type=SCAN_MARKET_TYPE,
            symbol=SYMBOL,
            captured_at=cap,
            exchange_ts=cap,
            bids=[],
            asks=[],
            best_bid=Decimal("41999"),
            best_ask=Decimal("42001"),
            bid_qty_sum=Decimal("1"),
            ask_qty_sum=Decimal("1"),
            spread_bps=Decimal("0.5"),
        )
    )
    db.add(
        OrderbookSnapshot(
            exchange="binance",
            market_type="SPOT",
            symbol=SYMBOL,
            captured_at=cap,
            exchange_ts=cap,
            bids=[],
            asks=[],
            best_bid=spot_mid - Decimal("1"),
            best_ask=spot_mid + Decimal("1"),
            bid_qty_sum=Decimal("1"),
            ask_qty_sum=Decimal("1"),
            spread_bps=Decimal("0.5"),
        )
    )


# --------------------------------------------------------------------------
# 单位一致性（纯函数）
# --------------------------------------------------------------------------


def test_skew_scalar_uses_decimal_oi_ratio() -> None:
    """OI 变化率是小数口径：0.3（+30%）必须能确认多空方向。

    旧实现拿 0.3 与阈值 1.0（百分数口径）比较，恒不成立。
    """
    # +30% OI 变化 + 正费率 -> 多头拥挤
    assert _skew_scalar(0.001, 0.3) == 0.001
    # +30% OI 变化 + 负费率 -> 空头拥挤
    assert _skew_scalar(-0.001, 0.3) == -0.001


def test_skew_scalar_requires_oi_expansion() -> None:
    """持仓没有明显扩张时不足以判定「拥挤」-> 标量为 0。"""
    # OI 仅 +0.5%（0.005 小数），低于 1% 确认阈值
    assert _skew_scalar(0.001, 0.005) == 0.0
    assert _skew_scalar(0.001, None) == 0.0


def test_skew_scalar_zero_funding_is_neutral() -> None:
    """费率为 0 -> 中性标量。"""
    assert _skew_scalar(0.0, 0.3) == 0.0


def test_percentile_of_uses_latest_value() -> None:
    """分位函数本身的口径：小于计数 + 相等计半。"""
    assert percentile_of([1.0, 2.0, 3.0], 3.0) == pytest.approx((2 + 0.5) / 3)
    assert percentile_of([1.0, 2.0, 3.0], 0.0) == 0.0


# --------------------------------------------------------------------------
# 取数层（真库）
# --------------------------------------------------------------------------


def test_basis_percentile_uses_latest_snapshot(db: Session) -> None:
    """基差分位必须以**最新**快照为当前值。

    历史快照基差很小（0.01%），最新快照基差很大（0.48%）。
    若误用最旧样本，当前值会落在低位，分位远低于 P95。
    """
    for minutes in [60, 30, 5]:
        spot_mid = Decimal("41800") if minutes == 5 else Decimal("41995")
        _add_book(db, minutes, spot_mid)
    db.commit()

    inputs = load_derivatives_inputs(db, symbol=SYMBOL, cutoff=NOW)
    assert inputs.basis_pct is not None
    assert inputs.basis_percentile is not None
    # 3 个样本：当前基差最高 -> 分位 = (2 + 0.5)/3 = 0.833
    # 若误用最旧样本（基差 0.012%），分位会掉到 0 附近
    assert inputs.basis_percentile > 0.8


def test_basis_extreme_rule_fires_with_latest_snapshot(db: Session) -> None:
    """基差处于历史 P95 以上时，规则必须真的触发。"""
    history_minutes = [60 + i * 5 for i in range(20)]
    for minutes in [*history_minutes, 5]:
        spot_mid = Decimal("41800") if minutes == 5 else Decimal("41995")
        _add_book(db, minutes, spot_mid)
    db.commit()

    ensure_rule_version(db, now=NOW)
    inputs = load_derivatives_inputs(db, symbol=SYMBOL, cutoff=NOW)
    outcome = scan_symbol(
        db,
        symbol=SYMBOL,
        cutoff=NOW,
        disabled_rules=[],
        delivered_keys=set(),
        last_fired_at={},
        derivatives=inputs,
    )
    rule_ids = [event.rule_id for event in outcome.new_events]
    assert "DER_BASIS_EXTREME" in rule_ids


def test_skew_percentile_uses_per_sample_oi_change(db: Session) -> None:
    """历史样本必须各配自己时点的 OI 变化率。

    历史费率样本的 OI 窗口内持仓不变 -> 标量为 0；
    当前费率配当前 OI 变化（+30%）-> 标量非 0。
    若历史样本套用当前 OI 变化，所有标量相同 -> 分位恒 0.5。
    """
    from aicrypto.infrastructure.database.models.market import (
        FundingHistory,
        OpenInterestHistory,
    )

    # 当前 OI：100 -> 130（+30%）
    for minutes, oi in [(70, Decimal("100")), (5, Decimal("130"))]:
        db.add(
            OpenInterestHistory(
                exchange="binance",
                market_type=SCAN_MARKET_TYPE,
                symbol=SYMBOL,
                ts=NOW - dt.timedelta(minutes=minutes),
                open_interest=oi,
                open_interest_value=oi,
            )
        )
    # 历史 OI：持仓不变
    for i in range(10):
        ts = NOW - dt.timedelta(hours=2 + i * 0.4)
        for offset in (0, 1):
            db.add(
                OpenInterestHistory(
                    exchange="binance",
                    market_type=SCAN_MARKET_TYPE,
                    symbol=SYMBOL,
                    ts=ts - dt.timedelta(hours=offset),
                    open_interest=Decimal("100"),
                    open_interest_value=Decimal("100"),
                )
            )
    # 当前费率与历史样本费率**完全相同**（+0.00001），这样两种实现的
    # 分位差异最大：
    # - 修复后：历史样本各配自己时点的 OI 变化（持仓不变 -> 0）-> 标量全为 0，
    #   当前标量 = +0.00001 -> 处于最高分位（约 1.0）；
    # - bug2（历史套用当前 OI 变化）：所有标量 = +0.00001 -> 分位恒 0.5。
    for hours, rate in [(1.2, Decimal("0.00001")), (0.2, Decimal("0.00001"))]:
        db.add(
            FundingHistory(
                exchange="binance",
                market_type=SCAN_MARKET_TYPE,
                symbol=SYMBOL,
                funding_time=NOW - dt.timedelta(hours=hours),
                funding_rate=rate,
                mark_price=Decimal("42000"),
            )
        )
    for i in range(10):
        db.add(
            FundingHistory(
                exchange="binance",
                market_type=SCAN_MARKET_TYPE,
                symbol=SYMBOL,
                funding_time=NOW - dt.timedelta(hours=2 + i * 0.4),
                funding_rate=Decimal("0.00001"),
                mark_price=Decimal("42000"),
            )
        )
    db.commit()

    inputs = load_derivatives_inputs(db, symbol=SYMBOL, cutoff=NOW)
    assert inputs.skew_percentile is not None
    # 修复后：历史样本各配自己时点的 OI 变化（持仓不变 -> 0）-> 标量全为 0；
    # 当前标量 = +0.00001 -> 处于**最高**分位（约 1.0）。
    # 若历史样本套用当前 OI 变化，所有标量相同 -> 分位恒 0.5。
    assert inputs.skew_percentile > 0.9, (
        f"当前多空标量应处于最高分位，实际 {inputs.skew_percentile}"
    )


def test_scan_uses_config_thresholds_by_default(db: Session, tmp_path, monkeypatch) -> None:
    """``scan_symbol`` 未显式传阈值时，必须**从 base.toml 加载**。

    若回落到硬编码默认值，「改配置不生效」的问题会在调用方忘记传参时
    静默复现——这正是 P3 修复前「配置化是假的」的变种。

    判据不能是「默认值 == 配置值」（二者恰好相同，无法区分），
    必须**改动配置值**并观察行为随之改变。
    """
    import aicrypto.services.anomaly_scan as scan_mod

    # 造一份阈值与项目默认不同的配置
    config_path = tmp_path / "base.toml"
    config_path.write_text(
        "[anomaly.rules.derivatives]\n"
        "oi_change_warn_pct = 99.0\n"
        "oi_change_critical_pct = 199.0\n"
        "funding_abs_warn = 0.05\n"
        "funding_abs_critical = 0.1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(scan_mod, "load_anomaly_rule_config", lambda: _fake_config(config_path))

    thresholds = scan_mod.load_derivatives_thresholds()
    assert thresholds.oi_change_warn_pct == 99.0
    assert thresholds.funding_abs_warn == 0.05

    # ``scan_symbol`` 不显式传阈值时，必须用**配置里的**阈值。
    # 造 OI +5%（低于配置阈值 99%）、费率 0.0008（低于配置阈值 5%）
    # 的数据——若回落到硬编码默认值（OI 3%、费率 0.05%），
    # 这两条都会触发。
    from aicrypto.infrastructure.database.models.market import (
        FundingHistory,
        OpenInterestHistory,
    )

    for minutes, oi in [(70, Decimal("100")), (5, Decimal("105"))]:
        db.add(
            OpenInterestHistory(
                exchange="binance",
                market_type=SCAN_MARKET_TYPE,
                symbol=SYMBOL,
                ts=NOW - dt.timedelta(minutes=minutes),
                open_interest=oi,
                open_interest_value=oi,
            )
        )
    for hours, rate in [(1.2, Decimal("0.0007")), (0.2, Decimal("0.0008"))]:
        db.add(
            FundingHistory(
                exchange="binance",
                market_type=SCAN_MARKET_TYPE,
                symbol=SYMBOL,
                funding_time=NOW - dt.timedelta(hours=hours),
                funding_rate=rate,
                mark_price=Decimal("42000"),
            )
        )
    db.commit()

    inputs = load_derivatives_inputs(db, symbol=SYMBOL, cutoff=NOW)
    outcome = scan_symbol(
        db,
        symbol=SYMBOL,
        cutoff=NOW,
        disabled_rules=[],
        delivered_keys=set(),
        last_fired_at={},
        derivatives=inputs,
        # **不传 derivatives_thresholds**——验证默认路径读配置
    )
    rules = {event.rule_id for event in outcome.new_events}
    assert "DER_OI_SURGE" not in rules, f"配置阈值应抑制 DER_OI_SURGE，实际 {rules}"
    assert "DER_FUNDING_EXTREME" not in rules, f"配置阈值应抑制 DER_FUNDING_EXTREME，实际 {rules}"


def _fake_config(path):
    """构造一个只含衍生品阈值的配置对象。"""
    from aicrypto.config.anomaly_rules import AnomalyRuleConfig
    from aicrypto.domain.anomaly.derivatives_rules import DerivativesThresholds

    return AnomalyRuleConfig(
        rule_version="v2",
        disabled_rules=[],
        derivatives=DerivativesThresholds(
            oi_change_warn_pct=99.0,
            oi_change_critical_pct=199.0,
            funding_abs_warn=0.05,
            funding_abs_critical=0.1,
        ),
    )


def test_oi_change_pct_supports_end_ts() -> None:
    """``oi_change_pct`` 必须支持指定窗口终点（历史分位需要）。"""
    from aicrypto.domain.market.derivatives import OpenInterestPoint, oi_change_pct

    base = NOW - dt.timedelta(hours=10)
    points = [
        OpenInterestPoint(ts=base, open_interest=Decimal("100")),
        OpenInterestPoint(ts=base + dt.timedelta(hours=1), open_interest=Decimal("110")),
        OpenInterestPoint(ts=base + dt.timedelta(hours=2), open_interest=Decimal("120")),
    ]
    # 以第 2 个点为终点：窗口起点是第 1 个点 -> 100 -> 110 = +10%
    mid = oi_change_pct(points, window_seconds=3600, end_ts=base + dt.timedelta(hours=1))
    assert mid == pytest.approx(0.1)
    # 以第 3 个点为终点：窗口起点是第 2 个点 -> 110 -> 120 = +9.09%
    # （窗口是「终点往前 1 小时内最晚的那个点」，不是第一个点）
    late = oi_change_pct(points, window_seconds=3600, end_ts=base + dt.timedelta(hours=2))
    assert late == pytest.approx(0.2 / 2.2)


def test_oi_change_pct_end_ts_before_first_point_returns_none() -> None:
    """终点早于所有样本时返回 None，不得当作 0。"""
    from aicrypto.domain.market.derivatives import OpenInterestPoint, oi_change_pct

    base = NOW - dt.timedelta(hours=10)
    points = [
        OpenInterestPoint(ts=base, open_interest=Decimal("100")),
        OpenInterestPoint(ts=base + dt.timedelta(hours=1), open_interest=Decimal("110")),
    ]
    assert oi_change_pct(points, window_seconds=3600, end_ts=base - dt.timedelta(hours=1)) is None
