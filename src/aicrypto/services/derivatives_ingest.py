"""衍生品与盘口数据入库编排（PRD 29.7 / 30.2）。

**幂等策略**（29.7）：

======================  ==========================================  ==================
表                      写入方式                                     幂等键
======================  ==========================================  ==================
``market_trades``       ``INSERT ... ON CONFLICT DO NOTHING``        ``(exchange, market_type, symbol, trade_id)``
``orderbook_snapshots`` 追加写                                      ``(exchange, market_type, symbol, captured_at)``
``open_interest_history`` ``INSERT ... ON CONFLICT DO UPDATE``      ``(exchange, market_type, symbol, ts)``
``funding_history``     ``INSERT ... ON CONFLICT DO NOTHING``        ``(exchange, market_type, symbol, funding_time)``
======================  ==========================================  ==================

**OI 用 DO UPDATE 而不是 DO NOTHING**：OI 是「当前值的采样」，
同一时刻重复轮询会得到略有差异的值；后到的更接近真实值，应覆盖。
**Funding 用 DO NOTHING**：资金费率是已结算的历史事实，改写即数据造假。

纯判定函数不做 IO，数据库写入集中在下方少数函数中。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aicrypto.common.ids import new_id
from aicrypto.domain.market.quality import DataQuality, Severity
from aicrypto.infrastructure.database.models.market import (
    FundingHistory,
    MarketDataQualityEvent,
    MarketTrade,
    OpenInterestHistory,
    OrderbookSnapshot,
)


class IngestOutcome(StrEnum):
    INSERTED = "INSERTED"
    UPDATED = "UPDATED"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True, slots=True)
class StaleCheck:
    """陈旧判定结果（29.4.3）。"""

    stale: bool
    staleness_seconds: float
    threshold_seconds: float
    rule_id: str = "DATA_STALE"


def evaluate_staleness(
    business_ts: dt.datetime,
    *,
    fetched_at: dt.datetime,
    threshold_seconds: float,
    rule_id: str = "DATA_STALE",
) -> StaleCheck:
    """按「业务时间 vs 拉取时间」判定陈旧（通路 C，29.4.3）。

    OI 允许滞后 3 分钟；Funding 当前值允许 2 分钟；Funding 历史允许 1 个周期。
    """
    age = max(0.0, (fetched_at - business_ts).total_seconds())
    return StaleCheck(
        stale=age > threshold_seconds,
        staleness_seconds=age,
        threshold_seconds=threshold_seconds,
        rule_id=rule_id,
    )


def build_stale_event(
    *,
    market_type: str,
    symbol: str,
    check: StaleCheck,
    detected_at: dt.datetime,
    detected_by: str = "REST",
) -> MarketDataQualityEvent:
    """构造 ``DATA_STALE`` 质量事件（CRITICAL / STALE）。

    ``detected_by`` 标明是哪条通路发现的陈旧：``REST`` 用于轮询类数据
    （OI、funding 历史），``WS`` 用于当前预估资金费率（来自 ``markPrice`` 流）。
    两者都走通路 C（29.4.3），但来源不同，排障时需要区分。
    """
    return MarketDataQualityEvent(
        id=new_id("dqe", now=detected_at),
        detected_at=detected_at,
        exchange="binance",
        market_type=market_type,
        symbol=symbol,
        timeframe=None,
        rule_id=check.rule_id,
        severity=Severity.CRITICAL.value,
        data_quality=DataQuality.STALE.value,
        detected_by=detected_by,
        context={
            "staleness_seconds": check.staleness_seconds,
            "threshold_seconds": check.threshold_seconds,
            "reason": "POLLED_DATA_STALE",
        },
    )


# --------------------------------------------------------------------------
# market_trades
# --------------------------------------------------------------------------


def persist_trade(db: Session, row: dict[str, Any]) -> IngestOutcome:
    """写入一条聚合成交。已存在同 ``trade_id`` 时跳过（29.7）。

    用 ``RETURNING`` 判断是否真正新增：**不依赖 ``rowcount``**，因为 SQLAlchemy
    的 ORM insert 会自动追加 ``RETURNING`` 取回主键，此时 psycopg 的
    ``cursor.rowcount`` 返回 ``-1``（未知）而非 0/1，真假判断会永远成立。
    冲突跳过时 ``RETURNING`` 不返回行，据此区分 INSERTED / DUPLICATE。
    """
    stmt = (
        insert(MarketTrade)
        .values(**row)
        .on_conflict_do_nothing(index_elements=["exchange", "market_type", "symbol", "trade_id"])
        .returning(MarketTrade.trade_id)
    )
    inserted = db.execute(stmt).scalar_one_or_none()
    return IngestOutcome.INSERTED if inserted is not None else IngestOutcome.DUPLICATE


# --------------------------------------------------------------------------
# orderbook_snapshots
# --------------------------------------------------------------------------


def persist_orderbook(db: Session, row: dict[str, Any]) -> IngestOutcome:
    """写入一条盘口快照。

    ``captured_at`` 是幂等键的一部分，因此同一时刻重复快照会被跳过。
    盘口快照是**追加写**（29.7），不做更新。
    """
    stmt = (
        insert(OrderbookSnapshot)
        .values(**row)
        .on_conflict_do_nothing(index_elements=["exchange", "market_type", "symbol", "captured_at"])
        .returning(OrderbookSnapshot.captured_at)
    )
    inserted = db.execute(stmt).scalar_one_or_none()
    return IngestOutcome.INSERTED if inserted is not None else IngestOutcome.DUPLICATE


# --------------------------------------------------------------------------
# open_interest_history
# --------------------------------------------------------------------------


def _oi_upsert(row: dict[str, Any]) -> Any:
    """构造 OI 的 ``DO UPDATE`` 语句。"""
    pk = ["exchange", "market_type", "symbol", "ts"]
    return (
        insert(OpenInterestHistory)
        .values(**row)
        .on_conflict_do_update(
            index_elements=pk,
            set_={
                "open_interest": row["open_interest"],
                "open_interest_value": row["open_interest_value"],
            },
        )
    )


def persist_open_interest(db: Session, row: dict[str, Any]) -> IngestOutcome:
    """写入一条持仓量采样。同一 ``ts`` 重复采样时**覆盖**（29.7）。

    OI 是当前值的采样而非历史事实：同一时刻两次轮询可能得到不同值，
    后到的更接近真实值，因此用 ``DO UPDATE``。
    """
    pk = ("exchange", "market_type", "symbol", "ts")
    existing = db.scalars(
        select(OpenInterestHistory).where(
            OpenInterestHistory.exchange == row["exchange"],
            OpenInterestHistory.market_type == row["market_type"],
            OpenInterestHistory.symbol == row["symbol"],
            OpenInterestHistory.ts == row["ts"],
        )
    ).first()
    if existing is None:
        stmt = (
            insert(OpenInterestHistory)
            .values(**row)
            .on_conflict_do_nothing(index_elements=list(pk))
            .returning(OpenInterestHistory.ts)
        )
        inserted = db.execute(stmt).scalar_one_or_none()
        if inserted is not None:
            return IngestOutcome.INSERTED
        # 并发插入竞争：回落到更新路径
        db.execute(_oi_upsert(row))
        return IngestOutcome.UPDATED
    db.execute(_oi_upsert(row))
    return IngestOutcome.UPDATED


# --------------------------------------------------------------------------
# funding_history
# --------------------------------------------------------------------------


def persist_funding_rate(db: Session, row: dict[str, Any]) -> IngestOutcome:
    """写入一条资金费率记录。已存在同 ``funding_time`` 时跳过（29.7）。

    资金费率是**已结算的历史事实**，改写即数据造假，因此用 ``DO NOTHING``。
    """
    stmt = (
        insert(FundingHistory)
        .values(**row)
        .on_conflict_do_nothing(
            index_elements=["exchange", "market_type", "symbol", "funding_time"]
        )
        .returning(FundingHistory.funding_time)
    )
    inserted = db.execute(stmt).scalar_one_or_none()
    return IngestOutcome.INSERTED if inserted is not None else IngestOutcome.DUPLICATE


# --------------------------------------------------------------------------
# 读取（特征计算用）
# --------------------------------------------------------------------------


def load_open_interest(
    db: Session,
    *,
    market_type: str,
    symbol: str,
    since: dt.datetime | None = None,
    as_of: dt.datetime | None = None,
    limit: int = 500,
) -> list[OpenInterestHistory]:
    """按时间升序读取持仓量采样。

    ``as_of`` 是**水位线**：只返回 ``ts <= as_of`` 的采样，避免把截止时刻
    之后才采集的数据算进历史分析（前视偏差）。
    """
    stmt = select(OpenInterestHistory).where(
        OpenInterestHistory.exchange == "binance",
        OpenInterestHistory.market_type == market_type,
        OpenInterestHistory.symbol == symbol.upper(),
    )
    if since is not None:
        stmt = stmt.where(OpenInterestHistory.ts >= since)
    if as_of is not None:
        stmt = stmt.where(OpenInterestHistory.ts <= as_of)
    rows = list(db.scalars(stmt.order_by(OpenInterestHistory.ts.desc()).limit(limit)))
    return list(reversed(rows))


def load_funding_rates(
    db: Session,
    *,
    market_type: str,
    symbol: str,
    since: dt.datetime | None = None,
    as_of: dt.datetime | None = None,
    limit: int = 200,
) -> list[FundingHistory]:
    """按时间升序读取资金费率记录。

    ``as_of`` 是**水位线**：只返回 ``funding_time <= as_of`` 的记录。
    """
    stmt = select(FundingHistory).where(
        FundingHistory.exchange == "binance",
        FundingHistory.market_type == market_type,
        FundingHistory.symbol == symbol.upper(),
    )
    if since is not None:
        stmt = stmt.where(FundingHistory.funding_time >= since)
    if as_of is not None:
        stmt = stmt.where(FundingHistory.funding_time <= as_of)
    rows = list(db.scalars(stmt.order_by(FundingHistory.funding_time.desc()).limit(limit)))
    return list(reversed(rows))


def load_latest_orderbook(
    db: Session,
    *,
    market_type: str,
    symbol: str,
    as_of: dt.datetime | None = None,
) -> OrderbookSnapshot | None:
    """读取最新一条盘口快照。

    ``as_of`` 是**水位线**：只返回 ``captured_at <= as_of`` 的快照，
    避免把截止时刻之后采集的盘口算进历史分析（前视偏差）。
    """
    stmt = select(OrderbookSnapshot).where(
        OrderbookSnapshot.exchange == "binance",
        OrderbookSnapshot.market_type == market_type,
        OrderbookSnapshot.symbol == symbol.upper(),
    )
    if as_of is not None:
        stmt = stmt.where(OrderbookSnapshot.captured_at <= as_of)
    return db.scalars(stmt.order_by(OrderbookSnapshot.captured_at.desc()).limit(1)).first()


def load_recent_trades(
    db: Session,
    *,
    market_type: str,
    symbol: str,
    since: dt.datetime | None = None,
    as_of: dt.datetime | None = None,
    limit: int = 1000,
) -> list[MarketTrade]:
    """按时间升序读取最近的聚合成交。

    ``as_of`` 是**水位线**：只返回 ``exchange_ts <= as_of`` 的成交。
    """
    stmt = select(MarketTrade).where(
        MarketTrade.exchange == "binance",
        MarketTrade.market_type == market_type,
        MarketTrade.symbol == symbol.upper(),
    )
    if since is not None:
        stmt = stmt.where(MarketTrade.exchange_ts >= since)
    if as_of is not None:
        stmt = stmt.where(MarketTrade.exchange_ts <= as_of)
    rows = list(db.scalars(stmt.order_by(MarketTrade.exchange_ts.desc()).limit(limit)))
    return list(reversed(rows))


def latest_oi_timestamp(
    db: Session,
    *,
    market_type: str,
    symbol: str,
    as_of: dt.datetime | None = None,
) -> dt.datetime | None:
    """最新一条持仓量采样的时间戳，用于陈旧判定。

    ``as_of`` 是**水位线**：只考虑 ``ts <= as_of`` 的采样。
    """
    stmt = select(OpenInterestHistory.ts).where(
        OpenInterestHistory.exchange == "binance",
        OpenInterestHistory.market_type == market_type,
        OpenInterestHistory.symbol == symbol.upper(),
    )
    if as_of is not None:
        stmt = stmt.where(OpenInterestHistory.ts <= as_of)
    return db.scalars(stmt.order_by(OpenInterestHistory.ts.desc()).limit(1)).first()


def latest_funding_timestamp(
    db: Session,
    *,
    market_type: str,
    symbol: str,
    as_of: dt.datetime | None = None,
) -> dt.datetime | None:
    """最新一条资金费率记录的时间戳。

    ``as_of`` 是**水位线**：只考虑 ``funding_time <= as_of`` 的记录。
    """
    stmt = select(FundingHistory.funding_time).where(
        FundingHistory.exchange == "binance",
        FundingHistory.market_type == market_type,
        FundingHistory.symbol == symbol.upper(),
    )
    if as_of is not None:
        stmt = stmt.where(FundingHistory.funding_time <= as_of)
    return db.scalars(stmt.order_by(FundingHistory.funding_time.desc()).limit(1)).first()


def decimals_to_float(value: Decimal | None) -> float | None:
    """``Decimal`` 转 ``float``；``None`` 原样返回（不填 0）。"""
    return None if value is None else float(value)


__all__ = [
    "IngestOutcome",
    "StaleCheck",
    "build_stale_event",
    "decimals_to_float",
    "evaluate_staleness",
    "latest_funding_timestamp",
    "latest_oi_timestamp",
    "load_funding_rates",
    "load_latest_orderbook",
    "load_open_interest",
    "load_recent_trades",
    "persist_funding_rate",
    "persist_open_interest",
    "persist_orderbook",
    "persist_trade",
]
