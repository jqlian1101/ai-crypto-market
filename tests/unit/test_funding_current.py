"""当前预估资金费率的跨进程共享存储测试（PRD 29.2 / 22.2.4 / L-05）。

**背景**：`markPrice@1s` 流携带当前预估资金费率，PRD 29.2 明确它是
``DER_FUNDING_EXTREME`` 的**低延迟触发源**，而 ``funding_history``
仍以 REST 为权威。

采集器与异动扫描器是**两个独立进程**，所以费率必须落到双方都能访问的
介质上。本项目用 Redis。

这些测试锁死三条不变量：

1. **不污染 ``funding_history``**——已结算事实不可改写（29.7）；
2. **键缺失 = 数据缺失**，不得被当作「费率为 0」（那会漏报极端费率）；
3. **TTL 必须大于陈旧阈值**，否则键会在数据变陈旧之前就消失。
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import pytest

from aicrypto.services import funding_current as fc

UTC = dt.UTC
NOW = dt.datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC)


def _snapshot(
    *,
    market_type: str = "USDM_FUTURES",
    symbol: str = "BTCUSDT",
    funding_rate: str = "0.0001",
    received_at: dt.datetime = NOW,
) -> fc.FundingCurrentSnapshot:
    return fc.FundingCurrentSnapshot(
        market_type=market_type,
        symbol=symbol,
        funding_rate=Decimal(funding_rate),
        mark_price=Decimal("42000.00"),
        index_price=Decimal("42001.00"),
        next_funding_time=NOW + dt.timedelta(hours=8),
        exchange_ts=received_at,
        received_at=received_at,
    )


# --------------------------------------------------------------------------
# 键约定
# --------------------------------------------------------------------------


def test_key_convention() -> None:
    """键名必须能区分市场与标的。"""
    key = fc.funding_current_key("USDM_FUTURES", "btcusdt")
    assert key == "market:funding_current:USDM_FUTURES:BTCUSDT"


def test_key_is_uppercased() -> None:
    assert fc.funding_current_key("USDM_FUTURES", "btcusdt") == fc.funding_current_key(
        "USDM_FUTURES", "BTCUSDT"
    )


def test_key_namespace_is_separate_from_funding_history() -> None:
    """当前值与已结算历史必须是不同的命名空间。"""
    assert fc.FUNDING_CURRENT_NAMESPACE == "market:funding_current"
    assert "funding_history" not in fc.FUNDING_CURRENT_NAMESPACE


# --------------------------------------------------------------------------
# TTL
# --------------------------------------------------------------------------


def test_ttl_exceeds_staleness_threshold() -> None:
    """TTL 必须大于陈旧阈值（29.4.3 通路 C 的 120 秒）。

    否则键会在「数据刚好变陈旧」之前消失，异动引擎无法区分
    「还没收到」与「收到了但很旧」。
    """
    from aicrypto.services.derivatives_collector import FUNDING_STALE_THRESHOLD_SECONDS

    assert fc.FUNDING_CURRENT_TTL_SECONDS > FUNDING_STALE_THRESHOLD_SECONDS


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------


def test_snapshot_roundtrip() -> None:
    """序列化后能完整还原。"""
    original = _snapshot()
    restored = fc.FundingCurrentSnapshot.from_json(original.to_json())
    assert restored.market_type == original.market_type
    assert restored.symbol == original.symbol
    assert restored.funding_rate == original.funding_rate
    assert restored.mark_price == original.mark_price
    assert restored.index_price == original.index_price
    assert restored.next_funding_time == original.next_funding_time
    assert restored.exchange_ts == original.exchange_ts
    assert restored.received_at == original.received_at


def test_snapshot_json_uses_decimal_strings() -> None:
    """Decimal 必须以字符串传输，不经浮点中转（30.2）。"""
    payload = json.loads(_snapshot(funding_rate="0.00012345").to_json())
    assert payload["funding_rate"] == "0.00012345"
    assert isinstance(payload["funding_rate"], str)
    assert payload["mark_price"] == "42000.00"


def test_snapshot_handles_none_optional_fields() -> None:
    """可选字段为 None 时也能往返。"""
    original = fc.FundingCurrentSnapshot(
        market_type="USDM_FUTURES",
        symbol="BTCUSDT",
        funding_rate=Decimal("0.0001"),
        mark_price=None,
        index_price=None,
        next_funding_time=None,
        exchange_ts=NOW,
        received_at=NOW,
    )
    restored = fc.FundingCurrentSnapshot.from_json(original.to_json())
    assert restored.mark_price is None
    assert restored.index_price is None
    assert restored.next_funding_time is None


# --------------------------------------------------------------------------
# 年龄计算
# --------------------------------------------------------------------------


def test_age_seconds_is_positive() -> None:
    assert _snapshot(received_at=NOW - dt.timedelta(seconds=30)).age_seconds(NOW) == 30.0


def test_age_seconds_never_negative() -> None:
    """时钟倒挂时年龄钳制为 0，不得为负。"""
    snapshot = _snapshot(received_at=NOW + dt.timedelta(minutes=5))
    assert snapshot.age_seconds(NOW) == 0.0


def test_age_seconds_zero_for_same_instant() -> None:
    assert _snapshot(received_at=NOW).age_seconds(NOW) == 0.0


# --------------------------------------------------------------------------
# 写入与读取（用内存假 Redis）
# --------------------------------------------------------------------------


class _FakeRedis:
    """最小 Redis 替身：只支持 set/get，够用即可。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        self.store[key] = value
        self.ttls[key] = ttl_seconds

    async def get(self, key: str) -> str | None:
        return self.store.get(key)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    redis = _FakeRedis()

    async def fake_cache_set(key: str, value: str, *, ttl_seconds: int = 300) -> None:
        await redis.set(key, value, ttl_seconds=ttl_seconds)

    async def fake_cache_get(key: str) -> str | None:
        return await redis.get(key)

    monkeypatch.setattr(fc, "cache_set", fake_cache_set)
    monkeypatch.setattr(fc, "cache_get", fake_cache_get)
    return redis


@pytest.mark.asyncio
async def test_write_then_read_roundtrip(fake_redis: _FakeRedis) -> None:
    """写入后能原样读回。"""
    await fc.write_funding_current(_snapshot())
    restored = await fc.read_funding_current("USDM_FUTURES", "BTCUSDT")
    assert restored is not None
    assert restored.funding_rate == Decimal("0.0001")
    assert restored.mark_price == Decimal("42000.00")


@pytest.mark.asyncio
async def test_write_uses_expected_key_and_ttl(fake_redis: _FakeRedis) -> None:
    await fc.write_funding_current(_snapshot())
    key = "market:funding_current:USDM_FUTURES:BTCUSDT"
    assert key in fake_redis.store
    assert fake_redis.ttls[key] == fc.FUNDING_CURRENT_TTL_SECONDS


@pytest.mark.asyncio
async def test_read_missing_key_returns_none(fake_redis: _FakeRedis) -> None:
    """键不存在 → None（数据缺失），绝不能返回「费率为 0」。"""
    result = await fc.read_funding_current("USDM_FUTURES", "BTCUSDT")
    assert result is None


@pytest.mark.asyncio
async def test_read_corrupted_payload_returns_none(fake_redis: _FakeRedis) -> None:
    """内容损坏等同于数据缺失——不能让一条坏数据打断整轮扫描。"""
    fake_redis.store["market:funding_current:USDM_FUTURES:BTCUSDT"] = "{not json"
    result = await fc.read_funding_current("USDM_FUTURES", "BTCUSDT")
    assert result is None


@pytest.mark.asyncio
async def test_read_incomplete_payload_returns_none(fake_redis: _FakeRedis) -> None:
    """字段缺失也当作数据缺失。"""
    fake_redis.store["market:funding_current:USDM_FUTURES:BTCUSDT"] = json.dumps(
        {"market_type": "USDM_FUTURES"}
    )
    result = await fc.read_funding_current("USDM_FUTURES", "BTCUSDT")
    assert result is None


@pytest.mark.asyncio
async def test_write_overwrites_previous_value(fake_redis: _FakeRedis) -> None:
    """后到的值覆盖先到的（当前值是最新观测）。"""
    await fc.write_funding_current(_snapshot(funding_rate="0.0001"))
    await fc.write_funding_current(_snapshot(funding_rate="0.0009"))
    restored = await fc.read_funding_current("USDM_FUTURES", "BTCUSDT")
    assert restored is not None
    assert restored.funding_rate == Decimal("0.0009")


@pytest.mark.asyncio
async def test_symbols_are_isolated(fake_redis: _FakeRedis) -> None:
    """不同标的不得互相覆盖。"""
    await fc.write_funding_current(_snapshot(symbol="BTCUSDT", funding_rate="0.0001"))
    await fc.write_funding_current(_snapshot(symbol="ETHUSDT", funding_rate="0.0002"))
    btc = await fc.read_funding_current("USDM_FUTURES", "BTCUSDT")
    eth = await fc.read_funding_current("USDM_FUTURES", "ETHUSDT")
    assert btc is not None and eth is not None
    assert btc.funding_rate == Decimal("0.0001")
    assert eth.funding_rate == Decimal("0.0002")


@pytest.mark.asyncio
async def test_market_types_are_isolated(fake_redis: _FakeRedis) -> None:
    """不同市场不得互相覆盖。"""
    await fc.write_funding_current(_snapshot(market_type="USDM_FUTURES", funding_rate="0.0001"))
    await fc.write_funding_current(_snapshot(market_type="SPOT", funding_rate="0.0002"))
    futures = await fc.read_funding_current("USDM_FUTURES", "BTCUSDT")
    spot = await fc.read_funding_current("SPOT", "BTCUSDT")
    assert futures is not None and spot is not None
    assert futures.funding_rate == Decimal("0.0001")
    assert spot.funding_rate == Decimal("0.0002")


# --------------------------------------------------------------------------
# 不污染已结算历史
# --------------------------------------------------------------------------


def test_no_funding_history_write_helper() -> None:
    """本模块不得提供写 ``funding_history`` 的入口。

    已结算的资金费率是历史事实，改写即数据造假（29.7）；
    当前预估费率只能走 Redis 共享通道。
    """
    forbidden = {
        "persist_funding_current",
        "write_funding_history",
        "upsert_funding_history",
    }
    assert forbidden.isdisjoint(set(dir(fc)))


def test_module_does_not_import_funding_history_model() -> None:
    """本模块不应直接依赖 ``FundingHistory`` 模型。"""
    import inspect

    source = inspect.getsource(fc)
    assert "FundingHistory" not in source
    assert "from aicrypto.infrastructure.database.models" not in source


# --------------------------------------------------------------------------
# 采集器接线
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collector_publishes_mark_price_to_shared_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """采集器收到 markPrice 后必须写入共享存储。"""
    import json as json_mod

    from aicrypto.config.settings import Settings
    from aicrypto.services.derivatives_collector import DerivativesCollector

    published: list[Any] = []

    async def fake_write(snapshot: Any) -> str:
        published.append(snapshot)
        return "key"

    monkeypatch.setattr("aicrypto.services.derivatives_collector.write_funding_current", fake_write)

    settings = Settings(
        DATABASE_URL="postgresql+psycopg://u:p@127.0.0.1:15432/db",
        REDIS_URL="redis://127.0.0.1:16379/0",
        AUTH_ENABLED=False,
        MARKET_SYMBOLS="BTCUSDT",
        MARKET_TYPES="USDM_FUTURES",
    )
    collector = DerivativesCollector(
        settings=settings,
        market_type="USDM_FUTURES",
        symbols=["BTCUSDT"],
        rest=None,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    raw = json_mod.dumps(
        {
            "e": "markPriceUpdate",
            "E": int(NOW.timestamp() * 1000),
            "s": "BTCUSDT",
            "p": "42000.00",
            "i": "42001.00",
            "r": "0.0001",
            "T": int((NOW + dt.timedelta(hours=8)).timestamp() * 1000),
        }
    )
    await collector._handle_message(raw)
    assert len(published) == 1
    snapshot = published[0]
    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.funding_rate == Decimal("0.0001")
    assert snapshot.mark_price == Decimal("42000.00")


@pytest.mark.asyncio
async def test_collector_publish_failure_does_not_break_ws_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis 写失败不得中断 WS 处理。

    Redis 只是共享通道，写不进去只是异动引擎少一个触发源，
    不能因此中断行情采集。
    """
    import json as json_mod

    from aicrypto.config.settings import Settings
    from aicrypto.services.derivatives_collector import DerivativesCollector

    async def fake_write(snapshot: Any) -> str:
        raise OSError("redis down")

    monkeypatch.setattr("aicrypto.services.derivatives_collector.write_funding_current", fake_write)

    settings = Settings(
        DATABASE_URL="postgresql+psycopg://u:p@127.0.0.1:15432/db",
        REDIS_URL="redis://127.0.0.1:16379/0",
        AUTH_ENABLED=False,
        MARKET_SYMBOLS="BTCUSDT",
        MARKET_TYPES="USDM_FUTURES",
    )
    collector = DerivativesCollector(
        settings=settings,
        market_type="USDM_FUTURES",
        symbols=["BTCUSDT"],
        rest=None,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    raw = json_mod.dumps(
        {
            "e": "markPriceUpdate",
            "E": int(NOW.timestamp() * 1000),
            "s": "BTCUSDT",
            "p": "42000.00",
            "i": "42001.00",
            "r": "0.0001",
        }
    )
    # 不得抛异常
    await collector._handle_message(raw)
    # 观测状态仍应更新（说明处理流程继续走完了）
    assert "BTCUSDT" in collector._funding_current_seen_at
