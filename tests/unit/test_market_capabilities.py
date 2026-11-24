"""市场能力分流的测试（PRD 29.2 / 29.6）。

**为什么重要**：持仓量、资金费率、标记价格都是**衍生品专属**数据，
现货市场没有。Binance 对现货调用 `/fapi/*` 端点会返回 **403**。
若放任请求发出，采集层会把它当成「REST 连续失败」而写出 **CRITICAL /
STALE** 级别的质量事件——那是虚假告警，会淹没真实的数据质量信号。

这些测试锁死两条不变量：

1. 现货**不得**发起任何衍生品请求（能力表 + client 守卫）；
2. 能力不匹配时抛的是**调用方逻辑错误**，绝不能被当成网络故障计数。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
import pytest

from aicrypto.config.settings import Settings
from aicrypto.infrastructure.binance_derivatives import (
    MARKET_CAPABILITIES,
    supports,
)
from aicrypto.infrastructure.binance_derivatives_client import (
    BinanceDerivativesClient,
    UnsupportedMarketError,
)
from aicrypto.infrastructure.rate_limiter import RateLimiter

SPOT = "SPOT"
FUTURES = "USDM_FUTURES"


def _settings(market_type: str) -> Settings:
    """构造测试用 Settings（绕过鉴权与 URL 强校验）。"""
    return Settings(
        DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/db",
        REDIS_URL="redis://localhost:6379/0",
        AUTH_ENABLED=False,
        MARKET_SYMBOLS="BTCUSDT",
        MARKET_TYPES=market_type,
    )


# --------------------------------------------------------------------------
# 能力表
# --------------------------------------------------------------------------


def test_spot_supports_only_depth_and_trades() -> None:
    assert supports(SPOT, "depth") is True
    assert supports(SPOT, "trades") is True
    assert supports(SPOT, "open_interest") is False
    assert supports(SPOT, "funding") is False
    assert supports(SPOT, "mark_price") is False


def test_futures_supports_all_capabilities() -> None:
    for capability in ("depth", "trades", "open_interest", "funding", "mark_price"):
        assert supports(FUTURES, capability) is True


def test_unknown_market_type_raises() -> None:
    with pytest.raises(ValueError, match="market_type"):
        supports("DUMMY", "depth")


def test_capability_table_covers_all_markets() -> None:
    """能力表必须覆盖所有已知 market_type。"""
    assert set(MARKET_CAPABILITIES) == {SPOT, FUTURES}


# --------------------------------------------------------------------------
# client 守卫：不得发出请求
# --------------------------------------------------------------------------


class _RecordingTransport(httpx.AsyncBaseTransport):
    """记录所有实际发出的请求。"""

    def __init__(self) -> None:
        self.requests: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        return httpx.Response(200, json={"openInterest": "1", "symbol": "BTCUSDT"})


def _client(transport: httpx.AsyncBaseTransport) -> BinanceDerivativesClient:
    http = httpx.AsyncClient(transport=transport)
    return BinanceDerivativesClient(http, RateLimiter())


@pytest.mark.asyncio
async def test_spot_oi_request_is_blocked_before_http() -> None:
    """现货请求持仓量时，不得发出任何 HTTP 请求。"""
    transport = _RecordingTransport()
    client = _client(transport)
    try:
        with pytest.raises(UnsupportedMarketError) as excinfo:
            await client.fetch_open_interest(market_type=SPOT, symbol="BTCUSDT")
        assert excinfo.value.market_type == SPOT
        assert excinfo.value.capability == "open_interest"
        assert transport.requests == []
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_spot_funding_request_is_blocked_before_http() -> None:
    transport = _RecordingTransport()
    client = _client(transport)
    try:
        with pytest.raises(UnsupportedMarketError):
            await client.fetch_funding_rate(market_type=SPOT, symbol="BTCUSDT")
        assert transport.requests == []
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_spot_oi_history_request_is_blocked_before_http() -> None:
    transport = _RecordingTransport()
    client = _client(transport)
    try:
        with pytest.raises(UnsupportedMarketError):
            await client.fetch_open_interest_history(market_type=SPOT, symbol="BTCUSDT")
        assert transport.requests == []
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_spot_premium_index_request_is_blocked_before_http() -> None:
    transport = _RecordingTransport()
    client = _client(transport)
    try:
        with pytest.raises(UnsupportedMarketError):
            await client.fetch_premium_index(market_type=SPOT, symbol="BTCUSDT")
        assert transport.requests == []
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_spot_depth_is_allowed() -> None:
    """现货的盘口深度必须照常请求——基差计算需要现货侧报价（29.6）。"""
    transport = _RecordingTransport()
    client = _client(transport)
    try:
        body = await client.fetch_depth(market_type=SPOT, symbol="BTCUSDT", limit=20)
        assert body == {"openInterest": "1", "symbol": "BTCUSDT"}
        assert len(transport.requests) == 1
        assert "/api/v3/depth" in transport.requests[0]
        assert "fapi" not in transport.requests[0]
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_futures_oi_request_is_allowed() -> None:
    """期货的持仓量请求照常发出。"""
    transport = _RecordingTransport()
    client = _client(transport)
    try:
        body = await client.fetch_open_interest(market_type=FUTURES, symbol="BTCUSDT")
        assert body["openInterest"] == "1"
        assert len(transport.requests) == 1
        assert "/fapi/v1/openInterest" in transport.requests[0]
    finally:
        await client._client.aclose()


def test_unsupported_error_carries_context() -> None:
    """异常要带上 market_type 与 capability，便于定位。"""
    err = UnsupportedMarketError(SPOT, "funding")
    assert err.market_type == SPOT
    assert err.capability == "funding"
    assert "SPOT" in str(err)
    assert "funding" in str(err)


# --------------------------------------------------------------------------
# 采集器：不得发起现货衍生品轮询
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spot_collector_skips_derivative_polls() -> None:
    """现货采集器不得调用任何衍生品接口。"""
    from aicrypto.services.derivatives_collector import DerivativesCollector

    called: list[str] = []

    class _FakeRest:
        async def fetch_open_interest(self, **kw: Any) -> dict[str, Any]:
            called.append("open_interest")
            return {"symbol": "BTCUSDT", "openInterest": "1", "time": 1}

        async def fetch_open_interest_history(self, **kw: Any) -> list[dict[str, Any]]:
            called.append("open_interest_history")
            return []

        async def fetch_funding_rate(self, **kw: Any) -> list[dict[str, Any]]:
            called.append("funding_rate")
            return []

        async def fetch_premium_index(self, **kw: Any) -> dict[str, Any]:
            called.append("premium_index")
            return {}

    settings = _settings(SPOT)
    collector = DerivativesCollector(
        settings=settings,
        market_type=SPOT,
        symbols=["BTCUSDT"],
        rest=_FakeRest(),  # type: ignore[arg-type]
    )

    await collector._poll_open_interest()
    await collector._poll_open_interest_history("BTCUSDT")
    await collector._poll_funding()

    assert called == []


@pytest.mark.asyncio
async def test_spot_collector_has_no_mark_price_stream() -> None:
    """现货不得订阅 markPrice 流（该流仅 USDⓈ-M 存在）。"""
    from aicrypto.services.derivatives_collector import DerivativesCollector

    collector = DerivativesCollector(
        settings=_settings(SPOT),
        market_type=SPOT,
        symbols=["BTCUSDT"],
        rest=None,  # type: ignore[arg-type]
    )
    assert "markPrice" not in collector._url
    assert "aggTrade" in collector._url


@pytest.mark.asyncio
async def test_futures_collector_subscribes_mark_price() -> None:
    """期货必须订阅 markPrice 流（29.2：资金费率低延迟触发源）。"""
    from aicrypto.services.derivatives_collector import DerivativesCollector

    collector = DerivativesCollector(
        settings=_settings(FUTURES),
        market_type=FUTURES,
        symbols=["BTCUSDT"],
        rest=None,  # type: ignore[arg-type]
    )
    assert "markPrice" in collector._url
    assert "aggTrade" in collector._url


def test_settings_market_types_default_includes_spot() -> None:
    """默认配置包含 SPOT——因此分流必须在代码层生效，不能依赖配置规避。"""
    settings = Settings(
        DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/db",
        REDIS_URL="redis://localhost:6379/0",
        AUTH_ENABLED=False,
    )
    assert "SPOT" in settings.MARKET_TYPES
    assert "USDM_FUTURES" in settings.MARKET_TYPES


def test_clock_is_not_used_directly() -> None:
    """守护：模块内不应直接调用 datetime.now()。"""
    import aicrypto.services.derivatives_collector as mod

    source = mod.__file__
    assert source is not None
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    assert "datetime.now(" not in text
    assert dt is not None
