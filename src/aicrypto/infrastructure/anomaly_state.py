"""异动去重与冷却状态的 Redis 适配器（PRD 22.4.4 / 22.4.4.1）。

- 去重键 ``dedup:{fingerprint}:{revision}``，TTL 24h，只在成功投递后写入；
- 冷却键 ``cooldown:{cooldown_key}``，TTL 为冷却窗口，值为触发时间（ISO）。

两者键空间独立，且与 ``cache.py`` 的通用 ``lock:`` 前缀隔离。

**revision 必须全程一致**（L-27）：

去重键的语义是 ``(fingerprint, revision)``——同一事件的 AI 解读升级投递
（``revision > 0``）是**另一次投递**，必须有自己的去重键。
若「检查时用 event.revision、写入时用 0」，则升级投递永远命中不了
自己写下的去重键，**每次扫描都会重新投递一遍**（通知轰炸）。
因此 ``revision`` 必须由调用方显式传入，**本模块不做任何默认值兜底**。
"""

from __future__ import annotations

import datetime as dt

from redis.asyncio import Redis

from aicrypto.domain.anomaly.dedup import DEDUP_TTL, cooldown_window, dedup_key

COOLDOWN_PREFIX = "cooldown:"


def _cooldown_redis_key(cooldown_key: str) -> str:
    return f"{COOLDOWN_PREFIX}{cooldown_key}"


async def load_delivered(client: Redis, fingerprints: list[str], *, revision: int) -> set[str]:
    """批量检查哪些去重键已存在（返回已投递的 dedup 键字符串）。

    ``revision`` 必须与写入时使用的 revision 一致，否则检查的是另一个键。
    **必填、不给默认值**——与 ``mark_delivered`` 口径一致：
    静默默认 0 会让「升级投递」用首投的键去查，
    查出「未投递」→ 重复投递（通知轰炸）。
    """
    if not fingerprints:
        return set()
    keys = [dedup_key(fp, revision) for fp in fingerprints]
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
    client: Redis,
    *,
    fingerprint: str,
    revision: int,
    cooldown_key: str,
    severity: str,
    fired_at: dt.datetime,
) -> None:
    """投递成功后写入去重键与冷却键。

    ``revision`` 必须与投递前检查时使用的 revision 一致——
    去重键是 ``(fingerprint, revision)``，写错 revision 等于写了一个
    永远不会被检查到的键，等于没写。

    冷却键只在**首投**（``revision == 0``）时写入：
    升级投递不占用冷却窗口（PRD 22.4.4.1 第 3 条）。
    """
    pipe = client.pipeline()
    pipe.set(dedup_key(fingerprint, revision), "1", ex=int(DEDUP_TTL.total_seconds()))
    if revision == 0:
        pipe.set(
            _cooldown_redis_key(cooldown_key),
            fired_at.isoformat(),
            ex=int(cooldown_window(severity).total_seconds()),
        )
    await pipe.execute()


__all__ = ["load_delivered", "load_last_fired", "mark_delivered"]
