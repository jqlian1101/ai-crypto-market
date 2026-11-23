"""REST K 线客户端测试：限流重试与分页（PRD 27.2）。"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import httpx
import pytest

from aicrypto.infrastructure.binance_rest import TIMEFRAME_MS
from aicrypto.infrastructure.binance_rest_client import BinanceKlineClient, closed_end_ms
from aicrypto.infrastructure.rate_limiter import RateLimitedError, RateLimiter

UTC = dt.UTC
T0_MS = 1_767_225_600_000  # 2026-01-01 00:00 UTC
STEP = TIMEFRAME_MS["5m"]
FETCHED = dt.datetime(2026, 1, 2, tzinfo=UTC)


def raw(i: int) -> list[Any]:
    open_ms = T0_MS + i * STEP
    return [
        open_ms, "100", "105", "95", "102", "10",
        open_ms + STEP - 1, "1000", 42, "4", "400", "0",
    ]  # fmt: skip


class FakeLimiter(RateLimiter):
    """不睡眠的限流器：记录申请与惩罚调用。"""

    def __init__(self) -> None:
        super().__init__({"SPOT": 10_000, "USDM_FUTURES": 10_000})
        self.acquired: list[tuple[str, int]] = []
        self.penalties: list[tuple[int, float | None]] = []

    async def acquire(self, market_type: str, weight: int) -> None:
        self.acquired.append((market_type, weight))

    def penalize(self, status: int, retry_after: float | None) -> float:
        self.penalties.append((status, retry_after))
        return 0.0


def make_client(handler: Any, limiter: FakeLimiter | None = None) -> BinanceKlineClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return BinanceKlineClient(http, limiter or FakeLimiter(), max_retries=3)


@pytest.mark.parametrize(
    ("market_type", "path"),
    [("SPOT", "/api/v3/time"), ("USDM_FUTURES", "/fapi/v1/time")],
)
async def test_fetch_server_time_uses_endpoint_per_market(market_type: str, path: str) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"serverTime": 1_767_225_600_123})

    limiter = FakeLimiter()
    client = make_client(handler, limiter)
    assert await client.fetch_server_time(market_type) == 1_767_225_600_123
    assert seen == [path]
    assert limiter.acquired == [(market_type, 1)]


async def test_fetch_server_time_rejects_bad_body() -> None:
    client = make_client(lambda r: httpx.Response(200, json={"foo": 1}))
    with pytest.raises(ValueError):
        await client.fetch_server_time("SPOT")


async def test_fetch_server_time_rejects_unknown_market() -> None:
    client = make_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        await client.fetch_server_time("COIN_FUTURES")


async def test_closed_end_ms_aligns_to_period_start() -> None:
    moment = dt.datetime(2026, 1, 1, 0, 7, 33, tzinfo=UTC)
    assert closed_end_ms(moment, "5m") == T0_MS + 5 * 60_000


async def test_fetch_paginates_and_parses() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        start = int(request.url.params["startTime"])
        rows = [raw(i) for i in range(3) if T0_MS + i * STEP >= start]
        return httpx.Response(200, json=rows)

    client = make_client(handler)
    rows = await client.fetch_closed_klines(
        market_type="USDM_FUTURES",
        symbol="BTCUSDT",
        timeframe="5m",
        start_ms=T0_MS,
        end_ms=T0_MS + 3 * STEP,
        fetched_at=FETCHED,
    )
    assert [r["open_time"] for r in rows] == sorted(r["open_time"] for r in rows)
    assert len(rows) == 3
    assert all(r["is_closed"] is True for r in rows)
    # 第一次请求 endTime 必须排除未收盘根
    assert int(seen[0]["endTime"]) == T0_MS + 3 * STEP - 1


async def test_fetch_drops_bar_at_or_after_end() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[raw(0), raw(1)])

    client = make_client(handler)
    rows = await client.fetch_closed_klines(
        market_type="SPOT",
        symbol="BTCUSDT",
        timeframe="5m",
        start_ms=T0_MS,
        end_ms=T0_MS + STEP,
        fetched_at=FETCHED,
    )
    assert len(rows) == 1


async def test_429_penalizes_and_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json=[raw(0)])

    limiter = FakeLimiter()
    client = make_client(handler, limiter)
    rows = await client.fetch_closed_klines(
        market_type="SPOT",
        symbol="BTCUSDT",
        timeframe="5m",
        start_ms=T0_MS,
        end_ms=T0_MS + STEP,
        fetched_at=FETCHED,
    )
    assert len(rows) == 1
    assert limiter.penalties == [(429, 7.0)]
    # 每次尝试前都经过限流申请
    assert len(limiter.acquired) == 2


async def test_persistent_418_raises_rate_limited() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(418)

    client = make_client(handler)
    with pytest.raises(RateLimitedError) as exc:
        await client.fetch_closed_klines(
            market_type="SPOT",
            symbol="BTCUSDT",
            timeframe="5m",
            start_ms=T0_MS,
            end_ms=T0_MS + STEP,
            fetched_at=FETCHED,
        )
    assert exc.value.status == 418


async def test_http_error_raises_without_retry() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    client = make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.fetch_closed_klines(
            market_type="SPOT",
            symbol="BTCUSDT",
            timeframe="5m",
            start_ms=T0_MS,
            end_ms=T0_MS + STEP,
            fetched_at=FETCHED,
        )
    assert calls["n"] == 1


async def test_weight_uses_kline_weight_table() -> None:
    limiter = FakeLimiter()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = make_client(handler, limiter)
    await client.fetch_closed_klines(
        market_type="USDM_FUTURES",
        symbol="BTCUSDT",
        timeframe="5m",
        start_ms=T0_MS,
        end_ms=T0_MS + STEP,
        fetched_at=FETCHED,
    )
    assert limiter.acquired == [("USDM_FUTURES", 5)]  # limit=1000 -> 5


async def test_non_list_response_rejected() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({"code": -1121, "msg": "bad"}))

    client = make_client(handler)
    with pytest.raises(ValueError, match="格式异常"):
        await client.fetch_closed_klines(
            market_type="SPOT",
            symbol="BAD",
            timeframe="5m",
            start_ms=T0_MS,
            end_ms=T0_MS + STEP,
            fetched_at=FETCHED,
        )
