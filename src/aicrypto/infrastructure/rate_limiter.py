"""Binance REST 统一限流（PRD 27.2）。

- 按接口权重扣减令牌桶，每个 market_type 独立预算（现货与合约的 IP 权重分开计算）；
- 429 / 418 进入全局退避：退避期间所有请求都等待，**不并发重试**；
- 时间源与 sleep 可注入，便于测试。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

#: Binance 官方每分钟 IP 权重上限：现货 6000，USDⓈ-M 2400。只用一半，给手工脚本留余量。
DEFAULT_WEIGHT_PER_MINUTE: dict[str, int] = {"SPOT": 3000, "USDM_FUTURES": 1200}

#: 418 表示 IP 已被封禁，Binance 通常给出较长的 Retry-After；缺省时按此值等待
DEFAULT_BAN_SECONDS = 120.0
DEFAULT_429_SECONDS = 30.0


def kline_weight(market_type: str, limit: int) -> int:
    """K 线接口权重（Binance 文档）。"""
    if market_type == "USDM_FUTURES":
        if limit < 100:
            return 1
        if limit < 500:
            return 2
        if limit <= 1000:
            return 5
        return 10
    return 2  # 现货 /api/v3/klines 固定 2


class RateLimitedError(Exception):
    """交易所返回 429 / 418。"""

    def __init__(self, status: int, retry_after: float | None) -> None:
        super().__init__(f"Binance 限流 HTTP {status}")
        self.status = status
        self.retry_after = retry_after


@dataclass(slots=True)
class _Bucket:
    capacity: float
    tokens: float
    updated: float


class RateLimiter:
    """按权重的令牌桶 + 全局退避。单进程内共享一个实例。"""

    def __init__(
        self,
        weight_per_minute: dict[str, int] | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        budgets = weight_per_minute or DEFAULT_WEIGHT_PER_MINUTE
        self._monotonic = monotonic
        self._sleep = sleep
        start = monotonic()
        self._buckets = {
            mt: _Bucket(capacity=float(cap), tokens=float(cap), updated=start)
            for mt, cap in budgets.items()
        }
        self._backoff_until = 0.0
        self._lock = asyncio.Lock()

    @property
    def backoff_remaining(self) -> float:
        return max(0.0, self._backoff_until - self._monotonic())

    def _refill(self, bucket: _Bucket) -> None:
        moment = self._monotonic()
        elapsed = moment - bucket.updated
        bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.capacity / 60.0)
        bucket.updated = moment

    async def acquire(self, market_type: str, weight: int) -> None:
        """阻塞直到可以发出一次权重为 ``weight`` 的请求。

        持锁等待：退避期内所有调用者排队，避免退避结束瞬间同时打出一批请求。
        """
        bucket = self._buckets.get(market_type)
        if bucket is None:
            raise ValueError(f"未配置限流预算的 market_type: {market_type!r}")
        if weight > bucket.capacity:
            raise ValueError(f"单次权重 {weight} 超过每分钟预算 {bucket.capacity}")
        async with self._lock:
            while True:
                wait = self.backoff_remaining
                if wait <= 0:
                    self._refill(bucket)
                    if bucket.tokens >= weight:
                        bucket.tokens -= weight
                        return
                    wait = (weight - bucket.tokens) * 60.0 / bucket.capacity
                await self._sleep(wait)

    def penalize(self, status: int, retry_after: float | None) -> float:
        """记录一次 429 / 418，返回进入退避的秒数。退避只延长、不缩短。"""
        default = DEFAULT_BAN_SECONDS if status == 418 else DEFAULT_429_SECONDS
        seconds = retry_after if retry_after and retry_after > 0 else default
        self._backoff_until = max(self._backoff_until, self._monotonic() + seconds)
        return seconds


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


__all__ = [
    "DEFAULT_WEIGHT_PER_MINUTE",
    "RateLimitedError",
    "RateLimiter",
    "kline_weight",
    "parse_retry_after",
]
