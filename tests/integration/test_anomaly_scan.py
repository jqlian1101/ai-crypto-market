"""异动扫描在 PostgreSQL 上的集成测试（PRD 22.4.4.1 / 30.6）。

需设置 ``ANOMALY_TEST_DATABASE_URL``（已执行迁移的测试库）。所有写入在事务内回滚。
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aicrypto.infrastructure.database.models.anomaly import AnomalyEvent
from aicrypto.infrastructure.database.models.market import MarketCandle
from aicrypto.services.anomaly_scan import scan_symbol

pytestmark = pytest.mark.integration

SYMBOL = "ZZTESTUSDT"
CUTOFF = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)
# 与 config/base.toml 的 disabled_rules 一致：1m 依赖规则必须显式禁用。
DISABLED = ["PRICE_PCT_1M", "PRICE_PCT_1M_CRIT", "PRICE_GAP", "PRICE_FLASH_MOVE"]


def _candle(
    i: int, close: Decimal, *, open_: Decimal | None = None, timeframe: str = "5m"
) -> MarketCandle:
    open_time = CUTOFF - dt.timedelta(minutes=5 * (200 - i))
    close_time = open_time + dt.timedelta(minutes=5)
    open_price = close if open_ is None else open_
    return MarketCandle(
        exchange="binance",
        market_type="USDM_FUTURES",
        symbol=SYMBOL,
        timeframe=timeframe,
        open_time=open_time,
        close_time=close_time,
        open=open_price,
        high=max(open_price, close) + Decimal("1"),
        low=min(open_price, close) - Decimal("1"),
        close=close,
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        trades_count=5,
        exchange_ts=close_time,
        received_at=close_time,
        processed_at=close_time,
        is_closed=True,
        source="REST",
    )


@pytest.fixture
def db() -> Session:
    url = os.getenv("ANOMALY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 ANOMALY_TEST_DATABASE_URL 以执行异动扫描集成测试")
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


def _seed_price_jump(db: Session) -> None:
    """前 199 根价格 100；最新根开 100、收 103：5m 涨幅 3%，触发 PRICE_PCT_5M_CRIT。"""
    for i in range(199):
        db.add(_candle(i, Decimal("100")))
    db.add(_candle(199, Decimal("103"), open_=Decimal("100")))
    db.flush()


def test_scan_persists_event_and_is_idempotent(db: Session) -> None:
    _seed_price_jump(db)
    delivered: set[str] = set()
    fired: dict[str, dt.datetime] = {}

    first = scan_symbol(
        db,
        symbol=SYMBOL,
        cutoff=CUTOFF,
        disabled_rules=DISABLED,
        delivered_keys=delivered,
        last_fired_at=fired,
    )
    assert first.persisted >= 1

    rows = list(db.scalars(select(AnomalyEvent).where(AnomalyEvent.symbol == SYMBOL)))
    assert any(r.rule_id == "PRICE_PCT_5M_CRIT" for r in rows)
    assert all(r.revision == 0 for r in rows)

    # 同一批数据、同一 cutoff 再扫一次：进程内状态被清空也不应产生第二条（唯一约束兜底）。
    second = scan_symbol(
        db,
        symbol=SYMBOL,
        cutoff=CUTOFF,
        disabled_rules=DISABLED,
        delivered_keys=set(),
        last_fired_at={},
    )
    assert second.persisted == 0
    count_after = len(list(db.scalars(select(AnomalyEvent).where(AnomalyEvent.symbol == SYMBOL))))
    assert count_after == len(rows)


def test_cooldown_suppresses_new_event_on_later_cutoff(db: Session) -> None:
    _seed_price_jump(db)
    fired = {f"{SYMBOL}:PRICE_PCT_5M_CRIT": CUTOFF - dt.timedelta(minutes=1)}

    outcome = scan_symbol(
        db,
        symbol=SYMBOL,
        cutoff=CUTOFF,
        disabled_rules=DISABLED,
        delivered_keys=set(),
        last_fired_at=fired,
    )
    assert outcome.suppressed.get("COOLDOWN", 0) >= 1
    assert not [
        r
        for r in db.scalars(select(AnomalyEvent).where(AnomalyEvent.symbol == SYMBOL))
        if r.rule_id == "PRICE_PCT_5M_CRIT"
    ]


def test_future_candles_are_excluded(db: Session) -> None:
    """cutoff 之后的 K 线不得影响结果（29.5 无未来函数）。"""
    _seed_price_jump(db)
    future = _candle(300, Decimal("500"), open_=Decimal("100"))
    future.close_time = CUTOFF + dt.timedelta(minutes=5)
    future.received_at = future.close_time
    future.processed_at = future.close_time
    future.open_time = CUTOFF
    db.add(future)
    db.flush()

    scan_symbol(
        db,
        symbol=SYMBOL,
        cutoff=CUTOFF,
        disabled_rules=DISABLED,
        delivered_keys=set(),
        last_fired_at={},
    )
    # 若未来根（close=500）被读取，5m 涨幅会是约 400%；真实涨幅为 3%。
    events = list(db.scalars(select(AnomalyEvent).where(AnomalyEvent.symbol == SYMBOL)))
    assert events, "应当检出 5m 异动"
    for event in events:
        change = event.metrics.get("price_change_pct")
        if change is not None:
            assert abs(float(change)) < 10, f"疑似读取了未来 K 线：{change}"
