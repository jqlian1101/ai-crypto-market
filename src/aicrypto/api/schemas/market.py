"""行情 HTTP 响应模型，价格保持十进制字符串。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

MarketType = Literal["SPOT", "USDM_FUTURES"]
Timeframe = Literal["1m", "5m", "15m", "1h", "4h"]


class CandlePayload(BaseModel):
    open_time: datetime
    close_time: datetime
    open: str
    high: str
    low: str
    close: str
    volume: str
    quote_volume: str
    trades_count: int
    source: str


class TimeframeOverview(BaseModel):
    timeframe: Timeframe
    latest: CandlePayload | None
    candle_count: int


class MarketOverview(BaseModel):
    symbol: str
    market_type: MarketType
    as_of: datetime
    timeframes: list[TimeframeOverview]


class CandleSeries(BaseModel):
    symbol: str
    market_type: MarketType
    timeframe: Timeframe
    candles: list[CandlePayload]
