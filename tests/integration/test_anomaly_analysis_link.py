"""异动 CRITICAL → analysis_runs 关联的真库端到端测试（PRD 22.6）。

锁住的不变量（任一被破坏都应让本文件失败）：

- CRITICAL 新事件产生后，恰好入队一条 ``trigger=ANOMALY`` 的分析运行；
- 该运行的 ``anomaly_event_id`` 外键指回触发它的事件，不靠幂等键字符串反推；
- 同一事件被重复扫描，不会再入队第二条；
- 迁移 ``0010`` 之后，``anomaly_event_id`` 外键必须指向存在的事件（不能悬空）。

需设置 ``ANOMALY_TEST_DATABASE_URL``（已执行迁移到 head 的测试库）。写入在事务内回滚。
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aicrypto.infrastructure.database.models.anomaly import AnomalyEvent
from aicrypto.infrastructure.database.models.market import MarketCandle
from aicrypto.infrastructure.database.models.report import AnalysisRunRecord
from aicrypto.services.anomaly_analysis import enqueue_critical_analyses
from aicrypto.services.anomaly_scan import scan_symbol

pytestmark = pytest.mark.integration

SYMBOL = "ZZLINKUSDT"
CUTOFF = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)
DISABLED = ["PRICE_PCT_1M", "PRICE_PCT_1M_CRIT", "PRICE_GAP", "PRICE_FLASH_MOVE"]


def _candle(i: int, close: Decimal, *, open_: Decimal | None = None) -> MarketCandle:
    open_time = CUTOFF - dt.timedelta(minutes=5 * (200 - i))
    close_time = open_time + dt.timedelta(minutes=5)
    open_price = close if open_ is None else open_
    return MarketCandle(
        exchange="binance",
        market_type="USDM_FUTURES",
        symbol=SYMBOL,
        timeframe="5m",
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
        pytest.skip("设置 ANOMALY_TEST_DATABASE_URL 以执行异动→分析关联集成测试")
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
    for i in range(199):
        db.add(_candle(i, Decimal("100")))
    db.add(_candle(199, Decimal("103"), open_=Decimal("100")))
    db.flush()


def _critical_events(db: Session) -> list[AnomalyEvent]:
    return list(
        db.scalars(
            select(AnomalyEvent).where(
                AnomalyEvent.symbol == SYMBOL,
                AnomalyEvent.severity == "CRITICAL",
            )
        )
    )


def _anomaly_runs(db: Session) -> list[AnalysisRunRecord]:
    return list(
        db.scalars(
            select(AnalysisRunRecord).where(
                AnalysisRunRecord.symbol == SYMBOL,
                AnalysisRunRecord.trigger == "ANOMALY",
            )
        )
    )


def test_critical_event_enqueues_exactly_one_linked_run(db: Session) -> None:
    _seed_price_jump(db)
    outcome = scan_symbol(
        db,
        symbol=SYMBOL,
        cutoff=CUTOFF,
        disabled_rules=DISABLED,
        delivered_keys=set(),
        last_fired_at={},
    )
    enqueue_critical_analyses(db, symbol=SYMBOL, events=outcome.new_events, now=CUTOFF)
    db.flush()

    events = _critical_events(db)
    runs = _anomaly_runs(db)
    assert len(events) >= 1
    assert len(runs) == len(events), "每个 CRITICAL 事件恰好入队一条分析"
    linked = {run.anomaly_event_id for run in runs}
    assert linked == {event.anomaly_event_id for event in events}, (
        "分析运行必须通过外键指回触发它的事件"
    )


def test_rescan_does_not_enqueue_duplicate_run(db: Session) -> None:
    _seed_price_jump(db)
    kwargs = {
        "symbol": SYMBOL,
        "cutoff": CUTOFF,
        "disabled_rules": DISABLED,
        "delivered_keys": set(),
        "last_fired_at": {},
    }
    first = scan_symbol(db, **kwargs)
    enqueue_critical_analyses(db, symbol=SYMBOL, events=first.new_events, now=CUTOFF)
    db.flush()
    before = len(_anomaly_runs(db))

    # 同一事件再次入队（幂等键相同）不得新增行。
    enqueue_critical_analyses(db, symbol=SYMBOL, events=first.new_events, now=CUTOFF)
    db.flush()
    assert len(_anomaly_runs(db)) == before


def test_dangling_anomaly_event_id_is_rejected(db: Session) -> None:
    """外键必须存在：指向不存在的事件应被数据库拒绝。"""
    # SAVEPOINT：外键失败只回滚这一条语句，外层测试事务保持有效，fixture 可正常 rollback。
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(
            AnalysisRunRecord(
                run_id="run_dangling_test",
                analysis_id="ana_dangling_test",
                symbol=SYMBOL,
                market_type="USDM_FUTURES",
                trigger="ANOMALY",
                idempotency_key="anomaly:dangling-test",
                anomaly_event_id="does-not-exist",
                created_at=CUTOFF,
            )
        )
        db.flush()
