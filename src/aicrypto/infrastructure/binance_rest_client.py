"""Binance 公共 REST K 线异步客户端（PRD 27.2 / 29.3.2）。

- 每次请求前向 :class:`RateLimiter` 申请权重；
- 429 / 418 进入全局退避后重试，不并发重试；其他 HTTP 错误直接抛出；
- 只返回已收盘 K 线：``endTime`` 截到当前周期起点之前。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from aicrypto.infrastructure.binance_rest import ENDPOINTS, TIMEFRAME_MS, parse_kline
from aicrypto.infrastructure.rate_limiter import (
    RateLimitedError,
    RateLimiter,
    kline_weight,
    parse_retry_after,
)

PAGE_LIMIT = 1000
MAX_RATE_LIMIT_RETRIES = 5


def closed_end_ms(now: datetime, timeframe: str) -> int:
    """当前周期起点（毫秒）。``open_time < 该值`` 的 K 线都已收盘。"""
    interval = TIMEFRAME_MS[timeframe]
    return (int(now.timestamp() * 1000) // interval) * interval


class BinanceKlineClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        limiter: RateLimiter,
        *,
        max_retries: int = MAX_RATE_LIMIT_RETRIES,
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._max_retries = max_retries

    async def _get_page(self, market_type: str, params: dict[str, Any]) -> list[list[Any]]:
        base_url, path = ENDPOINTS[market_type]
        weight = kline_weight(market_type, int(params["limit"]))
        for _ in range(self._max_retries + 1):
            await self._limiter.acquire(market_type, weight)
            resp = await self._client.get(f"{base_url}{path}", params=params)
            if resp.status_code in (429, 418):
                self._limiter.penalize(
                    resp.status_code, parse_retry_after(resp.headers.get("Retry-After"))
                )
                continue
            resp.raise_for_status()
            page = resp.json()
            if not isinstance(page, list):
                raise ValueError(f"K 线响应格式异常: {type(page).__name__}")
            return page
        raise RateLimitedError(resp.status_code, parse_retry_after(resp.headers.get("Retry-After")))

    async def fetch_closed_klines(
        self,
        *,
        market_type: str,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
        fetched_at: datetime,
        source: str = "REST",
    ) -> list[dict[str, Any]]:
        """拉取 ``[start_ms, end_ms)`` 内 open_time 的 K 线，按 open_time 升序。"""
        if market_type not in ENDPOINTS:
            raise ValueError(f"market_type 非法: {market_type!r}")
        interval = TIMEFRAME_MS[timeframe]
        rows: list[dict[str, Any]] = []
        cursor = start_ms
        while cursor < end_ms:
            page = await self._get_page(
                market_type,
                {
                    "symbol": symbol,
                    "interval": timeframe,
                    "startTime": cursor,
                    "endTime": end_ms - 1,  # 闭区间，排除未收盘那根
                    "limit": PAGE_LIMIT,
                },
            )
            if not page:
                break
            for raw in page:
                if int(raw[0]) >= end_ms:
                    continue
                rows.append(
                    parse_kline(
                        raw,
                        symbol=symbol,
                        market_type=market_type,
                        timeframe=timeframe,
                        fetched_at=fetched_at,
                        source=source,
                    )
                )
            next_cursor = int(page[-1][0]) + interval
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return rows


__all__ = ["PAGE_LIMIT", "BinanceKlineClient", "closed_end_ms"]
