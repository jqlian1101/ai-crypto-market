"""市场数据重连对账在 PostgreSQL 上的集成测试（PRD 29.3.2 / 29.4.2 / 29.9）。

需设置 ``ANOMALY_TEST_DATABASE_URL``（已执行迁移的测试库）。所有写入在事务内回滚。
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aicrypto.infrastructure.database.models.market import (
    MarketCandle,
    MarketDataQualityEvent,
)
from aicrypto.services.market_recovery import (
    DEGRADED_RULE_ID,
    GAP_RULE_ID,
    apply_plan,
    load_local_bars,
    reconcile,
)

pytestmark = pytest.mark.integration

SYMBOL = "ZZRECOVERYUSDT"
MARKET = "USDM_FUTURES"
TF = "5m"
BASE = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)
STEP = dt.timedelta(minutes=5)
NOW = BASE + dt.timedelta(hours=1)


def _bar_row(i: int, close: Decimal, *, source: str = "REST") -> dict[str, object]:
    open_time = BASE + STEP * i
    close_time = open_time + STEP
    return {
        "exchange": "binance",
        "market_type": MARKET,
        "symbol": SYMBOL,
        "timeframe": TF,
        "open_time": open_time,
        "close_time": close_time,
        "open": close,
        "high": close + Decimal("1"),
        "low": close - Decimal("1"),
        "close": close,
        "volume": Decimal("10"),
        "quote_volume": Decimal("1000"),
        "trades_count": 5,
        "exchange_ts": close_time,
        "received_at": close_time,
        "processed_at": close_time,
        "is_closed": True,
        "source": source,
    }


def _to_model(row: dict[str, object]) -> MarketCandle:
    return MarketCandle(**row)  # type: ignore[arg-type]


@pytest.fixture
def db() -> Session:
    url = os.getenv("ANOMALY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 ANOMALY_TEST_DATABASE_URL 以执行对账集成测试")
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


def test_recovery_backfills_gap_overwrites_divergence_and_keeps_orphans(db: Session) -> None:
    # 本地：0 号根（一致）、1 号根（分歧，本地为 WS 数据）、2 号根（REST 无，孤儿）
    db.add_all(
        [
            _to_model(_bar_row(0, Decimal("100"))),
            _to_model(_bar_row(1, Decimal("101"), source="WS")),
            _to_model(_bar_row(2, Decimal("999"), source="WS")),
        ]
    )
    db.flush()

    # REST：0、1（close 不同）、3（本地缺失）。2 号根不在 REST 覆盖内 -> 孤儿
    remote = [
        _bar_row(0, Decimal("100")),
        _bar_row(1, Decimal("102")),
        _bar_row(3, Decimal("103")),
    ]
    local = load_local_bars(
        db,
        market_type=MARKET,
        symbol=SYMBOL,
        timeframe=TF,
        since=BASE,
    )
    plan = reconcile(local, remote)

    result = apply_plan(
        db,
        plan,
        market_type=MARKET,
        symbol=SYMBOL,
        timeframe=TF,
        fetched=len(remote),
        detected_by="REST",
        now=NOW,
    )
    db.flush()

    assert result.missing == 1
    assert result.divergent == 1
    assert result.local_only == 1
    assert result.written == 2  # 缺失 1 + 分歧 1

    rows = {
        r.open_time: r
        for r in db.scalars(select(MarketCandle).where(MarketCandle.symbol == SYMBOL)).all()
    }
    # 分歧被 REST 覆盖
    assert rows[BASE + STEP].close == Decimal("102")
    assert rows[BASE + STEP].source == "REST"
    # 缺失被补齐
    assert rows[BASE + STEP * 3].close == Decimal("103")
    # 孤儿行不删除（29.9）
    assert rows[BASE + STEP * 2].close == Decimal("999")

    events = db.scalars(
        select(MarketDataQualityEvent).where(MarketDataQualityEvent.symbol == SYMBOL)
    ).all()
    rule_ids = sorted(e.rule_id for e in events)
    assert rule_ids == sorted([GAP_RULE_ID, "DATA_SOURCE_DIVERGENCE", DEGRADED_RULE_ID])


def test_recovery_is_idempotent_on_rerun(db: Session) -> None:
    remote = [_bar_row(0, Decimal("100")), _bar_row(1, Decimal("101"))]
    plan = reconcile([], remote)
    apply_plan(
        db,
        plan,
        market_type=MARKET,
        symbol=SYMBOL,
        timeframe=TF,
        fetched=2,
        detected_by="REST",
        now=NOW,
    )
    db.flush()

    local = load_local_bars(db, market_type=MARKET, symbol=SYMBOL, timeframe=TF, since=BASE)
    second = reconcile(local, remote)
    assert second.is_clean
