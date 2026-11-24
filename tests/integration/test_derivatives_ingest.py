"""衍生品与盘口入库幂等性的 PostgreSQL 集成测试（PRD 29.7）。

需设置 ``ANOMALY_TEST_DATABASE_URL``（已执行迁移的测试库）。所有写入在事务内回滚。

**核心断言**（29.7 的写入方式表）：

- ``market_trades`` 重复 ``trade_id`` 不产生重复行；
- ``open_interest_history`` 同一 ``ts`` **覆盖**（OI 是采样值）；
- ``funding_history`` 同一 ``funding_time`` **不覆盖**（已结算事实不可改写）；
- ``orderbook_snapshots`` 同一 ``captured_at`` 追加写去重。
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aicrypto.infrastructure.database.models.market import (
    FundingHistory,
    MarketTrade,
    OpenInterestHistory,
    OrderbookSnapshot,
)
from aicrypto.services.derivatives_ingest import (
    IngestOutcome,
    load_funding_rates,
    load_latest_orderbook,
    load_open_interest,
    load_recent_trades,
    persist_funding_rate,
    persist_open_interest,
    persist_orderbook,
    persist_trade,
)

pytestmark = pytest.mark.integration

SYMBOL = "ZZDERIVUSDT"
MARKET = "USDM_FUTURES"
BASE = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def db() -> Session:
    url = os.getenv("ANOMALY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 ANOMALY_TEST_DATABASE_URL 以执行衍生品幂等集成测试")
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


def _trade_row(trade_id: int, price: str = "42000.00") -> dict[str, object]:
    return {
        "exchange": "binance",
        "market_type": MARKET,
        "symbol": SYMBOL,
        "trade_id": trade_id,
        "price": Decimal(price),
        "quantity": Decimal("0.125"),
        "quote_quantity": Decimal(price) * Decimal("0.125"),
        "is_taker_buy": True,
        "exchange_ts": BASE,
        "received_at": BASE,
    }


def _oi_row(ts: dt.datetime, value: str = "1000") -> dict[str, object]:
    return {
        "exchange": "binance",
        "market_type": MARKET,
        "symbol": SYMBOL,
        "ts": ts,
        "open_interest": Decimal(value),
        "open_interest_value": None,
    }


def _funding_row(ts: dt.datetime, rate: str = "0.0001") -> dict[str, object]:
    return {
        "exchange": "binance",
        "market_type": MARKET,
        "symbol": SYMBOL,
        "funding_time": ts,
        "funding_rate": Decimal(rate),
        "mark_price": Decimal("42000.00"),
    }


def _book_row(captured_at: dt.datetime, bid: str = "42000.00") -> dict[str, object]:
    return {
        "exchange": "binance",
        "market_type": MARKET,
        "symbol": SYMBOL,
        "captured_at": captured_at,
        "exchange_ts": captured_at,
        "bids": [[bid, "1.5"]],
        "asks": [["42000.10", "0.9"]],
        "best_bid": Decimal(bid),
        "best_ask": Decimal("42000.10"),
        "bid_qty_sum": Decimal("1.5"),
        "ask_qty_sum": Decimal("0.9"),
        "spread_bps": Decimal("0.0238"),
    }


def test_trade_duplicate_is_ignored(db: Session) -> None:
    assert persist_trade(db, _trade_row(1)) is IngestOutcome.INSERTED
    assert persist_trade(db, _trade_row(1)) is IngestOutcome.DUPLICATE
    db.flush()
    rows = db.scalars(select(MarketTrade).where(MarketTrade.symbol == SYMBOL)).all()
    assert len(rows) == 1


def test_open_interest_same_timestamp_is_overwritten(db: Session) -> None:
    """OI 是「当前值的采样」，后到的更接近真实值，应覆盖。"""
    assert persist_open_interest(db, _oi_row(BASE, "1000")) is IngestOutcome.INSERTED
    assert persist_open_interest(db, _oi_row(BASE, "1050")) is IngestOutcome.UPDATED
    db.flush()
    rows = db.scalars(
        select(OpenInterestHistory).where(OpenInterestHistory.symbol == SYMBOL)
    ).all()
    assert len(rows) == 1
    assert rows[0].open_interest == Decimal("1050")


def test_funding_rate_same_time_is_not_overwritten(db: Session) -> None:
    """资金费率是已结算的历史事实，改写即数据造假。"""
    assert persist_funding_rate(db, _funding_row(BASE, "0.0001")) is IngestOutcome.INSERTED
    assert (
        persist_funding_rate(db, _funding_row(BASE, "0.9999")) is IngestOutcome.DUPLICATE
    )
    db.flush()
    rows = db.scalars(
        select(FundingHistory).where(FundingHistory.symbol == SYMBOL)
    ).all()
    assert len(rows) == 1
    assert rows[0].funding_rate == Decimal("0.0001")


def test_orderbook_same_timestamp_is_deduplicated(db: Session) -> None:
    assert persist_orderbook(db, _book_row(BASE)) is IngestOutcome.INSERTED
    assert persist_orderbook(db, _book_row(BASE)) is IngestOutcome.DUPLICATE
    db.flush()
    rows = db.scalars(
        select(OrderbookSnapshot).where(OrderbookSnapshot.symbol == SYMBOL)
    ).all()
    assert len(rows) == 1


def test_distinct_timestamps_are_kept(db: Session) -> None:
    for offset in range(3):
        ts = BASE + dt.timedelta(minutes=offset)
        assert persist_open_interest(db, _oi_row(ts)) is IngestOutcome.INSERTED
        assert persist_trade(db, _trade_row(offset)) is IngestOutcome.INSERTED
        assert persist_orderbook(db, _book_row(ts)) is IngestOutcome.INSERTED
    db.flush()
    assert (
        len(
            db.scalars(
                select(OpenInterestHistory).where(OpenInterestHistory.symbol == SYMBOL)
            ).all()
        )
        == 3
    )
    assert (
        len(db.scalars(select(MarketTrade).where(MarketTrade.symbol == SYMBOL)).all()) == 3
    )


# --------------------------------------------------------------------------
# 水位线（as_of）—— 防止前视偏差
# --------------------------------------------------------------------------


def test_orderbook_as_of_excludes_future_snapshot(db: Session) -> None:
    """``as_of`` 之后采集的盘口不得进入查询结果。"""
    early = BASE
    late = BASE + dt.timedelta(hours=1)
    assert persist_orderbook(db, _book_row(early)) is IngestOutcome.INSERTED
    assert persist_orderbook(db, _book_row(late)) is IngestOutcome.INSERTED
    db.flush()

    got = load_latest_orderbook(
        db, market_type=MARKET, symbol=SYMBOL, as_of=early + dt.timedelta(minutes=1)
    )
    assert got is not None
    assert got.captured_at == early


def test_open_interest_as_of_excludes_future_sample(db: Session) -> None:
    """``as_of`` 之后的持仓量采样不得进入查询结果。"""
    for offset in (0, 60, 120):
        assert (
            persist_open_interest(db, _oi_row(BASE + dt.timedelta(minutes=offset)))
            is IngestOutcome.INSERTED
        )
    db.flush()

    rows = load_open_interest(
        db,
        market_type=MARKET,
        symbol=SYMBOL,
        as_of=BASE + dt.timedelta(minutes=90),
    )
    assert [r.ts for r in rows] == [
        BASE,
        BASE + dt.timedelta(minutes=60),
    ]


def test_trades_as_of_excludes_future_trade(db: Session) -> None:
    """``as_of`` 之后的成交不得进入查询结果。"""
    for offset in (0, 60):
        row = _trade_row(offset)
        row["exchange_ts"] = BASE + dt.timedelta(minutes=offset)
        assert persist_trade(db, row) is IngestOutcome.INSERTED
    db.flush()

    trades = load_recent_trades(
        db,
        market_type=MARKET,
        symbol=SYMBOL,
        as_of=BASE + dt.timedelta(minutes=30),
    )
    assert len(trades) == 1


def test_funding_as_of_excludes_future_record(db: Session) -> None:
    """``as_of`` 之后的资金费率记录不得进入查询结果。"""
    for offset in (0, 60):
        assert (
            persist_funding_rate(
                db, _funding_row(BASE + dt.timedelta(minutes=offset), "0.0001")
            )
            is IngestOutcome.INSERTED
        )
    db.flush()

    rows = load_funding_rates(
        db, market_type=MARKET, symbol=SYMBOL, as_of=BASE + dt.timedelta(minutes=30)
    )
    assert len(rows) == 1
