"""写读往返验证：插入一根已收盘 K 线，再通过 API 读回。

用来确认「ORM ↔ 迁移 ↔ API」三层是通的（不只是接口能返回 200）。
"""

from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal

import httpx

from aicrypto.common.clock import now
from aicrypto.config.settings import get_settings
from aicrypto.infrastructure.database.models.market import MarketCandle
from aicrypto.infrastructure.database.session import get_engine

BASE = "http://127.0.0.1:8000"


def main() -> int:
    settings = get_settings()
    token = settings.AUTH_TOKEN.get_secret_value() if settings.AUTH_TOKEN else ""
    headers = {"Authorization": f"Bearer {token}"}

    base = now() - timedelta(hours=2)
    open_time = base.replace(minute=0, second=0, microsecond=0)
    close_time = open_time + timedelta(hours=1)

    candle = MarketCandle(
        exchange="binance",
        market_type="SPOT",
        symbol="BTCUSDT",
        timeframe="1h",
        open_time=open_time,
        close_time=close_time,
        open=Decimal("60000.00"),
        high=Decimal("61000.00"),
        low=Decimal("59500.00"),
        close=Decimal("60500.00"),
        volume=Decimal("123.456"),
        quote_volume=Decimal("7450000.00"),
        trades_count=4200,
        exchange_ts=close_time,
        received_at=close_time + timedelta(seconds=1),
        processed_at=close_time + timedelta(seconds=2),
        is_closed=True,
        source="REST_BACKFILL",
    )

    engine = get_engine(settings)
    with engine.begin() as conn:
        # 先清掉同键数据，保证可重复执行
        conn.execute(
            MarketCandle.__table__.delete().where(
                MarketCandle.symbol == "BTCUSDT",
                MarketCandle.timeframe == "1h",
                MarketCandle.open_time == open_time,
            )
        )
        conn.execute(
            MarketCandle.__table__.insert().values(
                {
                    "exchange": candle.exchange,
                    "market_type": candle.market_type,
                    "symbol": candle.symbol,
                    "timeframe": candle.timeframe,
                    "open_time": candle.open_time,
                    "close_time": candle.close_time,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                    "quote_volume": candle.quote_volume,
                    "trades_count": candle.trades_count,
                    "exchange_ts": candle.exchange_ts,
                    "received_at": candle.received_at,
                    "processed_at": candle.processed_at,
                    "is_closed": True,
                    "source": candle.source,
                }
            )
        )
    print(f"inserted candle: {candle.symbol} {candle.timeframe} open_time={open_time}")

    with httpx.Client(base_url=BASE, timeout=10.0) as client:
        r = client.get("/api/v1/market/candles?symbol=BTCUSDT&timeframe=1h", headers=headers)
        print(f"[candles] HTTP {r.status_code} -> {r.text[:500]}")

        r = client.get("/api/v1/market/overview?symbol=BTCUSDT", headers=headers)
        print(f"[overview] HTTP {r.status_code} -> {r.text[:500]}")

    # 清理
    with engine.begin() as conn:
        conn.execute(
            MarketCandle.__table__.delete().where(
                MarketCandle.symbol == "BTCUSDT",
                MarketCandle.timeframe == "1h",
                MarketCandle.open_time == open_time,
            )
        )
    print("cleanup done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
