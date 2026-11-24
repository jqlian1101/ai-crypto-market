"""衍生品与盘口采集常驻进程（PRD 29.2 / 29.7）。

轮询线（REST：OI / Funding / 盘口 / 合约规格）与流线（WS：aggTrade / markPrice）
在同一个进程内运行，共享一个 REST 限流器（27.2）。

用法（从项目根目录运行）::

    .venv\\Scripts\\python.exe scripts\\run_derivatives_collector.py
    .venv\\Scripts\\python.exe scripts\\run_derivatives_collector.py --market-types USDM_FUTURES

Ctrl+C 退出。退出码：0 正常停止，1 启动失败。

.. note::

   **Open Interest 没有 WebSocket 推送**（29.2），只能 REST 轮询，
   因此衍生品异动的检出延迟高于价格异动（29.4.3）。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys

import httpx

from aicrypto.config.market_symbols import resolve_market_symbols
from aicrypto.config.settings import Settings, get_settings
from aicrypto.infrastructure.binance_derivatives import BASE_URLS
from aicrypto.infrastructure.binance_derivatives_client import BinanceDerivativesClient
from aicrypto.infrastructure.cache import close_redis
from aicrypto.infrastructure.logging import get_logger
from aicrypto.infrastructure.rate_limiter import RateLimiter
from aicrypto.services.derivatives_collector import DerivativesCollector

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Binance 衍生品与盘口采集")
    parser.add_argument("--market-types", default=None, help="覆盖 MARKET_TYPES，逗号分隔")
    return parser


async def run(settings: Settings, market_types: list[str]) -> None:
    symbols = resolve_market_symbols(settings)
    limiter = RateLimiter()
    stop = asyncio.Event()
    async with httpx.AsyncClient(timeout=15.0) as http:
        rest = BinanceDerivativesClient(http, limiter)
        collectors = [
            DerivativesCollector(
                settings=settings,
                market_type=mt,
                symbols=symbols,
                rest=rest,
                stop_event=stop,
            )
            for mt in market_types
        ]
        logger.info(
            "衍生品采集启动",
            market_types=market_types,
            symbols=symbols,
            oi_poll_seconds=settings.MARKET_OI_POLL_SECONDS,
            book_interval_seconds=settings.MARKET_BOOK_SNAPSHOT_INTERVAL_SECONDS,
        )
        try:
            await asyncio.gather(*(c.run() for c in collectors))
        finally:
            stop.set()
            await close_redis()


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    args = build_parser().parse_args(argv)
    raw = args.market_types or settings.MARKET_TYPES
    market_types = [m.strip().upper() for m in raw.split(",") if m.strip()]
    invalid = [m for m in market_types if m not in BASE_URLS]
    if not market_types or invalid:
        print(f"market_type 非法: {invalid or raw}", file=sys.stderr, flush=True)
        return 1
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(settings, market_types))
    return 0


if __name__ == "__main__":
    sys.exit(main())
