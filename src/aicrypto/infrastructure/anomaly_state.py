"""异动去重与冷却状态的 Redis 适配器（PRD 22.4.4 / 22.4.4.1）。

- 去重键 ``dedup:{fingerprint}:{revision}``，TTL 24h，只在成功投递后写入；
- 冷却键 ``cooldown:{cooldown_key}``，TTL 为冷却窗口，值为触发时间（ISO）。

两者键空间独立，且与 ``cache.py`` 的通用 ``lock:`` 前缀隔离。
"""

from __future__ import annotations

import datetime as dt

from redis.asyncio import Redis

from aicrypto.domain.anomaly.dedup import DEDUP_TTL, cooldown_window, dedup_key

COOLDOWN_PREFIX = "cooldown:"


def _cooldown_redis_key(cooldown_key: str) -> str:
    return f"{COOLDOWN_PREFIX}{cooldown_key}"


async def load_delivered(client: Redis, fingerprints: list[str]) -> set[str]:
    """批量检查哪些去重键已存在（返回已投递的 dedup 键字符串）。"""
    if not fingerprints:
        return set()
    keys = [dedup_key(fp, 0) for fp in fingerprints]
    pipe = client.pipeline()
    for key in keys:
        pipe.exists(key)
    flags = await pipe.execute()
    return {key for key, flag in zip(keys, flags, strict=True) if flag}


async def load_last_fired(client: Redis, cooldown_keys: list[str]) -> dict[str, dt.datetime]:
    """读取各冷却键的最近触发时间；不存在的键不返回。"""
    if not cooldown_keys:
        return {}
    pipe = client.pipeline()
    for key in cooldown_keys:
        pipe.get(_cooldown_redis_key(key))
    values = await pipe.execute()
    out: dict[str, dt.datetime] = {}
    for key, value in zip(cooldown_keys, values, strict=True):
        if value is not None:
            out[key] = dt.datetime.fromisoformat(value)
    return out


async def mark_delivered(
    client: Redis, *, fingerprint: str, cooldown_key: str, severity: str, fired_at: dt.datetime
) -> None:
    """首投成功后写入去重键与冷却键。"""
    pipe = client.pipeline()
    pipe.set(dedup_key(fingerprint, 0), "1", ex=int(DEDUP_TTL.total_seconds()))
    pipe.set(
        _cooldown_redis_key(cooldown_key),
        fired_at.isoformat(),
        ex=int(cooldown_window(severity).total_seconds()),
    )
    await pipe.execute()


__all__ = ["load_delivered", "load_last_fired", "mark_delivered"]
