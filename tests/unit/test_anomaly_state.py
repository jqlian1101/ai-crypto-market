"""去重/冷却 Redis 适配器测试（PRD 22.4.4 / 22.4.4.1 / L-27）。

锁死三条不变量：

1. **去重键必须是 ``(fingerprint, revision)``**——
   写入时用的 revision 必须与检查时用的完全一致，
   否则写下的键永远不会被检查到，升级投递会被反复重投；
2. **升级投递不占用冷却窗口**（22.4.4.1 第 3 条）；
3. **去重键空间与冷却键空间互相独立**。

Redis 用最小替身实现（只支持 set/get/exists/pipeline）。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from aicrypto.infrastructure import anomaly_state as st

UTC = dt.UTC
NOW = dt.datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
FP = "ae_abc123"


class _FakePipe:
    def __init__(self, store: dict[str, str], ttls: dict[str, int]) -> None:
        self._store = store
        self._ttls = ttls
        self._ops: list[tuple[str, str, str | None, int | None]] = []

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._ops.append(("set", key, value, ex))

    def exists(self, key: str) -> None:
        self._ops.append(("exists", key, None, None))

    def get(self, key: str) -> None:
        self._ops.append(("get", key, None, None))

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for op, key, value, ex in self._ops:
            if op == "set":
                assert value is not None
                self._store[key] = value
                if ex is not None:
                    self._ttls[key] = ex
                results.append(True)
            elif op == "exists":
                results.append(key in self._store)
            else:
                results.append(self._store.get(key))
        self._ops.clear()
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def pipeline(self) -> _FakePipe:
        return _FakePipe(self.store, self.ttls)


@pytest.fixture
def redis() -> _FakeRedis:
    return _FakeRedis()


# --------------------------------------------------------------------------
# 去重键的 revision 必须一致（L-27）
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_key_uses_given_revision(redis: _FakeRedis) -> None:
    await st.mark_delivered(
        redis,
        fingerprint=FP,
        revision=3,
        cooldown_key="BTCUSDT:PRICE_PCT_5M",
        severity="WARN",
        fired_at=NOW,
    )

    assert "dedup:ae_abc123:3" in redis.store
    assert "dedup:ae_abc123:0" not in redis.store


@pytest.mark.asyncio
async def test_dedup_key_roundtrips_through_load_delivered(redis: _FakeRedis) -> None:
    """写入后必须能被同一个 revision 的加载函数查到——否则等于没写。"""
    await st.mark_delivered(
        redis,
        fingerprint=FP,
        revision=2,
        cooldown_key="BTCUSDT:PRICE_PCT_5M",
        severity="WARN",
        fired_at=NOW,
    )

    hit = await st.load_delivered(redis, [FP], revision=2)
    assert hit == {"dedup:ae_abc123:2"}


@pytest.mark.asyncio
async def test_load_delivered_with_wrong_revision_finds_nothing(redis: _FakeRedis) -> None:
    """不同 revision 是不同的投递——用错 revision 必须查不到。"""
    await st.mark_delivered(
        redis,
        fingerprint=FP,
        revision=2,
        cooldown_key="BTCUSDT:PRICE_PCT_5M",
        severity="WARN",
        fired_at=NOW,
    )

    assert await st.load_delivered(redis, [FP], revision=0) == set()


@pytest.mark.asyncio
async def test_upgrade_delivery_does_not_consume_cooldown(redis: _FakeRedis) -> None:
    """22.4.4.1 第 3 条：升级投递（revision>0）不占用冷却窗口。"""
    await st.mark_delivered(
        redis,
        fingerprint=FP,
        revision=1,
        cooldown_key="BTCUSDT:PRICE_PCT_5M",
        severity="WARN",
        fired_at=NOW,
    )

    assert "cooldown:BTCUSDT:PRICE_PCT_5M" not in redis.store


@pytest.mark.asyncio
async def test_first_delivery_writes_cooldown(redis: _FakeRedis) -> None:
    await st.mark_delivered(
        redis,
        fingerprint=FP,
        revision=0,
        cooldown_key="BTCUSDT:PRICE_PCT_5M",
        severity="WARN",
        fired_at=NOW,
    )

    assert "cooldown:BTCUSDT:PRICE_PCT_5M" in redis.store
    assert redis.store["cooldown:BTCUSDT:PRICE_PCT_5M"] == NOW.isoformat()


@pytest.mark.asyncio
async def test_cooldown_and_dedup_key_spaces_are_independent(redis: _FakeRedis) -> None:
    """去重键前缀与冷却键前缀不得互相污染。"""
    await st.mark_delivered(
        redis,
        fingerprint=FP,
        revision=0,
        cooldown_key="BTCUSDT:PRICE_PCT_5M",
        severity="WARN",
        fired_at=NOW,
    )

    dedup_keys = [k for k in redis.store if k.startswith("dedup:")]
    cooldown_keys = [k for k in redis.store if k.startswith("cooldown:")]

    assert dedup_keys == ["dedup:ae_abc123:0"]
    assert cooldown_keys == ["cooldown:BTCUSDT:PRICE_PCT_5M"]


@pytest.mark.asyncio
async def test_load_last_fired_reads_cooldown(redis: _FakeRedis) -> None:
    await st.mark_delivered(
        redis,
        fingerprint=FP,
        revision=0,
        cooldown_key="BTCUSDT:PRICE_PCT_5M",
        severity="WARN",
        fired_at=NOW,
    )

    fired = await st.load_last_fired(redis, ["BTCUSDT:PRICE_PCT_5M"])
    assert fired == {"BTCUSDT:PRICE_PCT_5M": NOW}


@pytest.mark.asyncio
async def test_empty_input_returns_empty(redis: _FakeRedis) -> None:
    assert await st.load_delivered(redis, [], revision=0) == set()
    assert await st.load_last_fired(redis, []) == {}


# --------------------------------------------------------------------------
# NOTIFY_DEDUP_TTL_SECONDS 接线
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configured_dedup_ttl_is_written(redis: _FakeRedis) -> None:
    """注入的去重 TTL 必须真正写入 Redis，而不是被 DEDUP_TTL 默认值吞掉。"""
    await st.mark_delivered(
        redis,
        fingerprint=FP,
        revision=0,
        cooldown_key="BTCUSDT:PRICE_PCT_5M",
        severity="WARN",
        fired_at=NOW,
        dedup_ttl_seconds=3600,
    )

    assert redis.ttls["dedup:ae_abc123:0"] == 3600


@pytest.mark.asyncio
async def test_dedup_ttl_defaults_to_prd_when_not_injected(redis: _FakeRedis) -> None:
    await st.mark_delivered(
        redis,
        fingerprint=FP,
        revision=0,
        cooldown_key="BTCUSDT:PRICE_PCT_5M",
        severity="WARN",
        fired_at=NOW,
    )

    assert redis.ttls["dedup:ae_abc123:0"] == int(st.DEDUP_TTL.total_seconds())


@pytest.mark.asyncio
async def test_zero_dedup_ttl_skips_dedup_key_without_error(redis: _FakeRedis) -> None:
    """配置为 0 表示关闭去重：不写去重键（Redis 会拒绝 EX 0），但冷却键照常写入。"""
    await st.mark_delivered(
        redis,
        fingerprint=FP,
        revision=0,
        cooldown_key="BTCUSDT:PRICE_PCT_5M",
        severity="WARN",
        fired_at=NOW,
        dedup_ttl_seconds=0,
    )

    assert not any(k.startswith("dedup:") for k in redis.store)
    assert "cooldown:BTCUSDT:PRICE_PCT_5M" in redis.store
