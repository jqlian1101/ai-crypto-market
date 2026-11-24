"""Binance 衍生品 REST 异步客户端（PRD 27.2 / 29.2）。

与 :class:`aicrypto.infrastructure.binance_rest_client.BinanceKlineClient` 同构：

- 每次请求前向 :class:`RateLimiter` 申请权重；
- 429 / 418 进入全局退避后重试，**不并发重试**；
- 所有权重都走统一限流器，与 K 线采集共享预算。

**OI 与 Funding 的时效**（29.4.3）：两者都是 REST 轮询，没有 WS 可对照，
因此用「拉取时间 vs 业务时间」判断陈旧，由调用方产生 ``DATA_STALE`` 事件。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

from aicrypto.infrastructure.binance_derivatives import (
    BASE_URLS,
    DEPTH_MAX_LIMIT,
    FUNDING_PAGE_LIMIT,
    FUNDING_RATE_PATH,
    FUTURES_DEPTH_PATH,
    FUTURES_EXCHANGE_INFO_PATH,
    OI_HISTORY_PAGE_LIMIT,
    OI_HISTORY_PATH,
    OI_PATH,
    PREMIUM_INDEX_PATH,
    SPOT_DEPTH_PATH,
    SPOT_EXCHANGE_INFO_PATH,
    endpoint_weight,
)
from aicrypto.infrastructure.rate_limiter import (
    RateLimitedError,
    RateLimiter,
    parse_retry_after,
)

MAX_RATE_LIMIT_RETRIES = 5


class BinanceDerivativesClient:
    """衍生品与盘口 REST 客户端。"""

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

    async def _get(
        self,
        market_type: str,
        path: str,
        *,
        endpoint: str,
        params: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> Any:
        base_url = BASE_URLS[market_type]
        weight = endpoint_weight(endpoint, market_type=market_type, limit=limit)
        for _ in range(self._max_retries + 1):
            await self._limiter.acquire(market_type, weight)
            resp = await self._client.get(f"{base_url}{path}", params=params)
            if resp.status_code in (429, 418):
                self._limiter.penalize(
                    resp.status_code, parse_retry_after(resp.headers.get("Retry-After"))
                )
                continue
            resp.raise_for_status()
            return resp.json()
        raise RateLimitedError(resp.status_code, parse_retry_after(resp.headers.get("Retry-After")))

    # ------------------------------------------------------------------ OI

    async def fetch_open_interest(self, *, market_type: str, symbol: str) -> dict[str, Any]:
        """当前持仓量。"""
        body = await self._get(
            market_type,
            OI_PATH,
            endpoint="open_interest",
            params={"symbol": symbol.upper()},
        )
        if not isinstance(body, dict):
            raise ValueError(f"持仓量响应格式异常: {type(body).__name__}")
        return body

    async def fetch_open_interest_history(
        self,
        *,
        market_type: str,
        symbol: str,
        period: str = "5m",
        limit: int = OI_HISTORY_PAGE_LIMIT,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """持仓量历史。``period`` 只能是 ``5m`` / ``15m`` / ``30m`` / ``1h`` / ``2h`` / ``4h`` / ``6h`` / ``12h`` / ``1d``。"""
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "period": period,
            "limit": limit,
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        body = await self._get(
            market_type,
            OI_HISTORY_PATH,
            endpoint="open_interest_history",
            params=params,
        )
        if not isinstance(body, list):
            raise ValueError(f"持仓量历史响应格式异常: {type(body).__name__}")
        return body

    # ------------------------------------------------------------- Funding

    async def fetch_funding_rate(
        self,
        *,
        market_type: str,
        symbol: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = FUNDING_PAGE_LIMIT,
    ) -> list[dict[str, Any]]:
        """资金费率历史。"""
        params: dict[str, Any] = {"symbol": symbol.upper(), "limit": limit}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        body = await self._get(
            market_type,
            FUNDING_RATE_PATH,
            endpoint="funding_rate",
            params=params,
        )
        if not isinstance(body, list):
            raise ValueError(f"资金费率响应格式异常: {type(body).__name__}")
        return body

    async def fetch_premium_index(
        self, *, market_type: str, symbol: str | None = None
    ) -> dict[str, Any]:
        """标记价格、指数价格与当前预估资金费率。

        不传 ``symbol`` 时返回全部合约（响应是数组）。
        """
        params = {"symbol": symbol.upper()} if symbol else None
        body = await self._get(
            market_type,
            PREMIUM_INDEX_PATH,
            endpoint="premium_index",
            params=params,
        )
        if isinstance(body, list):
            if not body:
                raise ValueError("标记价格响应为空")
            return body[0]
        if not isinstance(body, dict):
            raise ValueError(f"标记价格响应格式异常: {type(body).__name__}")
        return body

    # ---------------------------------------------------------------- 盘口

    async def fetch_depth(
        self,
        *,
        market_type: str,
        symbol: str,
        limit: int = DEPTH_MAX_LIMIT,
    ) -> dict[str, Any]:
        """盘口深度快照。"""
        path = FUTURES_DEPTH_PATH if market_type == "USDM_FUTURES" else SPOT_DEPTH_PATH
        body = await self._get(
            market_type,
            path,
            endpoint="depth",
            params={"symbol": symbol.upper(), "limit": limit},
            limit=limit,
        )
        if not isinstance(body, dict):
            raise ValueError(f"深度响应格式异常: {type(body).__name__}")
        return body

    # ------------------------------------------------------------ 合约规格

    async def fetch_exchange_info(self, *, market_type: str) -> dict[str, Any]:
        """合约规格与精度。"""
        path = (
            FUTURES_EXCHANGE_INFO_PATH if market_type == "USDM_FUTURES" else SPOT_EXCHANGE_INFO_PATH
        )
        body = await self._get(market_type, path, endpoint="exchange_info")
        if not isinstance(body, dict):
            raise ValueError(f"合约规格响应格式异常: {type(body).__name__}")
        return body


def stale_seconds(business_ts: dt.datetime, *, fetched_at: dt.datetime) -> float:
    """业务时间与拉取时间之差（秒），用于陈旧判定（29.4.3）。"""
    return max(0.0, (fetched_at - business_ts).total_seconds())


__all__ = ["BinanceDerivativesClient", "stale_seconds"]
