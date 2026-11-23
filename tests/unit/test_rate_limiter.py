"""REST 统一限流测试（PRD 27.2）。"""

from __future__ import annotations

import asyncio

import pytest

from aicrypto.infrastructure.rate_limiter import (
    RateLimiter,
    kline_weight,
    parse_retry_after,
)


class FakeTime:
    """可控单调时钟；sleep 只推进时间并记录。"""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def make_limiter(budget: int = 60) -> tuple[RateLimiter, FakeTime]:
    clock = FakeTime()
    limiter = RateLimiter({"SPOT": budget}, monotonic=clock.monotonic, sleep=clock.sleep)
    return limiter, clock


async def test_acquire_within_budget_does_not_wait() -> None:
    limiter, clock = make_limiter(60)
    for _ in range(30):
        await limiter.acquire("SPOT", 2)
    assert clock.sleeps == []


async def test_acquire_waits_for_refill_when_exhausted() -> None:
    limiter, clock = make_limiter(60)  # 1 权重/秒
    await limiter.acquire("SPOT", 60)
    await limiter.acquire("SPOT", 5)
    assert clock.sleeps == [pytest.approx(5.0)]


async def test_429_backoff_blocks_all_callers() -> None:
    limiter, clock = make_limiter(60)
    assert limiter.penalize(429, 10.0) == 10.0
    await limiter.acquire("SPOT", 1)
    assert clock.t >= 10.0
    assert limiter.backoff_remaining == 0.0


async def test_418_uses_long_default_without_retry_after() -> None:
    limiter, _ = make_limiter()
    assert limiter.penalize(418, None) == 120.0


async def test_backoff_only_extends() -> None:
    limiter, _ = make_limiter()
    limiter.penalize(418, None)
    limiter.penalize(429, 1.0)
    assert limiter.backoff_remaining == pytest.approx(120.0)


async def test_concurrent_callers_serialized_during_backoff() -> None:
    """退避结束后调用者按序放行，不并发重试（27.2）。"""
    limiter, clock = make_limiter(60)
    limiter.penalize(429, 3.0)
    order: list[int] = []

    async def caller(i: int) -> None:
        await limiter.acquire("SPOT", 30)
        order.append(i)

    await asyncio.gather(*(caller(i) for i in range(3)))
    assert order == [0, 1, 2]
    # 第三个请求必须等到桶补满 30 权重：3s 退避 + 30s 补充
    assert clock.t == pytest.approx(33.0)


async def test_unknown_market_or_oversized_weight_rejected() -> None:
    limiter, _ = make_limiter(10)
    with pytest.raises(ValueError):
        await limiter.acquire("USDM_FUTURES", 1)
    with pytest.raises(ValueError):
        await limiter.acquire("SPOT", 11)


@pytest.mark.parametrize(
    ("market_type", "limit", "expected"),
    [
        ("SPOT", 1000, 2),
        ("USDM_FUTURES", 99, 1),
        ("USDM_FUTURES", 499, 2),
        ("USDM_FUTURES", 1000, 5),
        ("USDM_FUTURES", 1500, 10),
    ],
)
def test_kline_weight(market_type: str, limit: int, expected: int) -> None:
    assert kline_weight(market_type, limit) == expected


def test_parse_retry_after() -> None:
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None
