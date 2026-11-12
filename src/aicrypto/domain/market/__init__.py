"""行情领域包。"""

from __future__ import annotations

from aicrypto.domain.market.models import (
    FundingHistory,
    InstrumentMetadata,
    MarketCandle,
    MarketDataQualityEvent,
    MarketDataSyncRun,
    MarketTrade,
    OpenInterestHistory,
    OrderbookSnapshot,
)
from aicrypto.domain.market.quality import (
    Candle,
    DataQuality,
    QualityEvent,
    Severity,
    align_to_timeframe,
    evaluate_staleness,
    find_gaps,
    gap_severity,
    interval_seconds,
    is_candle_closed,
    is_taker_buy,
    validate_candle,
    validate_ohlc,
)

__all__ = [
    "Candle",
    "DataQuality",
    "FundingHistory",
    "InstrumentMetadata",
    "MarketCandle",
    "MarketDataQualityEvent",
    "MarketDataSyncRun",
    "MarketTrade",
    "OpenInterestHistory",
    "OrderbookSnapshot",
    "QualityEvent",
    "Severity",
    "align_to_timeframe",
    "evaluate_staleness",
    "find_gaps",
    "gap_severity",
    "interval_seconds",
    "is_candle_closed",
    "is_taker_buy",
    "validate_candle",
    "validate_ohlc",
]
