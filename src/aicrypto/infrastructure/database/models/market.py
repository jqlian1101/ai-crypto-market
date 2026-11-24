"""行情持久化模型（PRD 第 30.2 章），不属于纯领域层。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Integer, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from aicrypto.infrastructure.database.base import (
    Base,
    BpsType,
    PriceType,
    QuantityType,
    RateType,
    bool_column,
    ck,
    desc_idx,
    idx,
    json_column,
    pk,
)

MARKET_TYPES = ("SPOT", "USDM_FUTURES")
TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h")
SOURCES = ("WS", "REST", "REST_BACKFILL")
SEVERITIES = ("INFO", "WARN", "CRITICAL")
DATA_QUALITY_STATES = ("GOOD", "DEGRADED", "STALE", "MISSING", "INVALID")
DETECTED_BY = ("WS", "REST", "CROSSCHECK", "SCHEDULER", "SYSTEM")


class MarketCandle(Base):
    """已收盘 K 线；写入前必须校验 close_time <= now。"""

    __tablename__ = "market_candles"
    exchange: Mapped[str] = mapped_column(Text, nullable=False, default="binance")
    market_type: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    open_time: Mapped[dt.datetime] = mapped_column(nullable=False)
    close_time: Mapped[dt.datetime] = mapped_column(nullable=False)
    open: Mapped[Decimal] = mapped_column(PriceType, nullable=False)
    high: Mapped[Decimal] = mapped_column(PriceType, nullable=False)
    low: Mapped[Decimal] = mapped_column(PriceType, nullable=False)
    close: Mapped[Decimal] = mapped_column(PriceType, nullable=False)
    volume: Mapped[Decimal] = mapped_column(QuantityType, nullable=False)
    quote_volume: Mapped[Decimal] = mapped_column(QuantityType, nullable=False)
    trades_count: Mapped[int] = mapped_column(Integer, nullable=False)
    taker_buy_base_volume: Mapped[Decimal | None] = mapped_column(PriceType, nullable=True)
    taker_buy_quote_volume: Mapped[Decimal | None] = mapped_column(PriceType, nullable=True)
    exchange_ts: Mapped[dt.datetime] = mapped_column(nullable=False)
    received_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    processed_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    is_closed: Mapped[bool] = bool_column(default=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        pk("pk_market_candles", "exchange", "market_type", "symbol", "timeframe", "open_time"),
        desc_idx("idx_candles_lookup", "symbol", "market_type", "timeframe", "close_time"),
        idx("idx_candles_processed", "processed_at"),
        ck("market_candles_market_type", "market_type IN ('SPOT','USDM_FUTURES')"),
        ck("market_candles_timeframe", "timeframe IN ('1m','5m','15m','1h','4h')"),
        ck("market_candles_source", "source IN ('WS','REST','REST_BACKFILL')"),
        ck("market_candles_ohlc", "high >= greatest(open, close, low) AND high >= low"),
        ck("market_candles_volume", "volume >= 0 AND quote_volume >= 0"),
    )


class MarketTrade(Base):
    """聚合成交；is_taker_buy = NOT Binance isBuyerMaker。"""

    __tablename__ = "market_trades"
    exchange: Mapped[str] = mapped_column(Text, nullable=False, default="binance")
    market_type: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    trade_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price: Mapped[Decimal] = mapped_column(PriceType, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QuantityType, nullable=False)
    quote_quantity: Mapped[Decimal] = mapped_column(PriceType, nullable=False)
    is_taker_buy: Mapped[bool] = bool_column(default=False)
    exchange_ts: Mapped[dt.datetime] = mapped_column(nullable=False)
    received_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    __table_args__ = (
        pk("pk_market_trades", "exchange", "market_type", "symbol", "trade_id"),
        desc_idx("idx_trades_window", "symbol", "market_type", "exchange_ts"),
        ck("market_trades_market_type", "market_type IN ('SPOT','USDM_FUTURES')"),
    )


class OrderbookSnapshot(Base):
    """盘口快照（只存 top20）。"""

    __tablename__ = "orderbook_snapshots"
    exchange: Mapped[str] = mapped_column(Text, nullable=False, default="binance")
    market_type: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    exchange_ts: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    bids: Mapped[Any] = json_column()
    asks: Mapped[Any] = json_column()
    best_bid: Mapped[Decimal] = mapped_column(PriceType, nullable=False)
    best_ask: Mapped[Decimal] = mapped_column(PriceType, nullable=False)
    bid_qty_sum: Mapped[Decimal] = mapped_column(QuantityType, nullable=False)
    ask_qty_sum: Mapped[Decimal] = mapped_column(QuantityType, nullable=False)
    spread_bps: Mapped[Decimal] = mapped_column(BpsType, nullable=False)
    __table_args__ = (
        pk("pk_orderbook_snapshots", "exchange", "market_type", "symbol", "captured_at"),
        desc_idx("idx_books_symbol", "symbol", "market_type", "captured_at"),
    )


class OpenInterestHistory(Base):
    """持仓量历史（REST 轮询）。"""

    __tablename__ = "open_interest_history"
    exchange: Mapped[str] = mapped_column(Text, nullable=False, default="binance")
    market_type: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[dt.datetime] = mapped_column(nullable=False)
    open_interest: Mapped[Decimal] = mapped_column(QuantityType, nullable=False)
    open_interest_value: Mapped[Decimal | None] = mapped_column(PriceType, nullable=True)
    __table_args__ = (
        pk("pk_open_interest_history", "exchange", "market_type", "symbol", "ts"),
        desc_idx("idx_oi_lookup", "symbol", "market_type", "ts"),
    )


class FundingHistory(Base):
    """资金费率历史。"""

    __tablename__ = "funding_history"
    exchange: Mapped[str] = mapped_column(Text, nullable=False, default="binance")
    market_type: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    funding_time: Mapped[dt.datetime] = mapped_column(nullable=False)
    funding_rate: Mapped[Decimal] = mapped_column(RateType, nullable=False)
    mark_price: Mapped[Decimal] = mapped_column(PriceType, nullable=False)
    __table_args__ = (
        pk("pk_funding_history", "exchange", "market_type", "symbol", "funding_time"),
        desc_idx("idx_funding_lookup", "symbol", "market_type", "funding_time"),
    )


class InstrumentMetadata(Base):
    """合约规格与精度。"""

    __tablename__ = "instrument_metadata"
    exchange: Mapped[str] = mapped_column(Text, nullable=False, default="binance")
    market_type: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    price_precision: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quantity_precision: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    tick_size: Mapped[Decimal] = mapped_column(PriceType, nullable=False)
    step_size: Mapped[Decimal] = mapped_column(PriceType, nullable=False)
    min_qty: Mapped[Decimal] = mapped_column(PriceType, nullable=False)
    min_notional: Mapped[Decimal] = mapped_column(PriceType, nullable=False)
    contract_multiplier: Mapped[Decimal] = mapped_column(
        PriceType, nullable=False, default=Decimal("1"), server_default="1"
    )
    quote_asset: Mapped[str] = mapped_column(Text, nullable=False)
    margin_asset: Mapped[str | None] = mapped_column(Text, nullable=True)
    onboard_date: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    raw: Mapped[Any] = json_column()
    __table_args__ = (pk("pk_instrument_metadata", "exchange", "market_type", "symbol"),)


class MarketDataQualityEvent(Base):
    """数据质量事件。"""

    __tablename__ = "market_data_quality_events"
    id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    detected_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    exchange: Mapped[str] = mapped_column(Text, nullable=False)
    market_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeframe: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    data_quality: Mapped[str] = mapped_column(Text, nullable=False)
    detected_by: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[Any] = json_column()
    resolved_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    __table_args__ = (
        idx("idx_quality_events_detected", "detected_at"),
        idx("idx_quality_events_symbol", "symbol", "rule_id"),
        ck("market_data_quality_events_severity", "severity IN ('INFO','WARN','CRITICAL')"),
        ck(
            "market_data_quality_events_quality",
            "data_quality IN ('GOOD','DEGRADED','STALE','MISSING','INVALID')",
        ),
        ck(
            "market_data_quality_events_detected_by",
            "detected_by IN ('WS','REST','CROSSCHECK','SCHEDULER','SYSTEM')",
        ),
    )


class MarketDataSyncRun(Base):
    """采集同步运行记录。"""

    __tablename__ = "market_data_sync_runs"
    id: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    exchange: Mapped[str] = mapped_column(
        Text, nullable=False, default="binance", server_default="binance"
    )
    market_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    candles_written: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    gaps_detected: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error: Mapped[Any] = json_column(nullable=True)
    detail: Mapped[Any] = json_column(nullable=True)
    __table_args__ = (
        idx("idx_sync_runs_started", "started_at"),
        ck("market_data_sync_runs_status", "status IN ('RUNNING','SUCCESS','FAILED','PARTIAL')"),
    )


__all__ = [
    "DATA_QUALITY_STATES",
    "DETECTED_BY",
    "MARKET_TYPES",
    "SEVERITIES",
    "SOURCES",
    "TIMEFRAMES",
    "FundingHistory",
    "InstrumentMetadata",
    "MarketCandle",
    "MarketDataQualityEvent",
    "MarketDataSyncRun",
    "MarketTrade",
    "OpenInterestHistory",
    "OrderbookSnapshot",
]
