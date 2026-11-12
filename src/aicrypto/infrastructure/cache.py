"""Redis 封装（缓存 / 队列）。

PRD 9.2：Redis 用于 Cache / Queue。首期主要用途：

- 调度任务锁（``SCHEDULER_LOCK_MODE=local`` 时的单机锁）
- 通知渠道限流余量状态
- 快照内容缓存（13.3：计算结果可缓存并标明版本）
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis

from aicrypto.config.settings import Settings, get_settings

_client: Redis | None = None


def get_redis(settings: Settings | None = None) -> Redis:
    """获取 Redis 客户端单例。"""
    global _client
    if _client is None:
        cfg = settings or get_settings()
        _client = Redis.from_url(
            cfg.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
        )
    return _client


async def close_redis() -> None:
    """关闭连接。"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def cache_get(key: str) -> str | None:
    client = get_redis()
    return await client.get(key)


async def cache_set(key: str, value: str, *, ttl_seconds: int = 300) -> None:
    client = get_redis()
    await client.set(key, value, ex=ttl_seconds)


async def cache_delete(key: str) -> None:
    client = get_redis()
    await client.delete(key)


async def acquire_lock(key: str, *, ttl_seconds: int = 60) -> bool:
    """获取分布式锁（单实例可用 local 模式）。"""
    client = get_redis()
    return bool(await client.set(f"lock:{key}", "1", nx=True, ex=ttl_seconds))


async def release_lock(key: str) -> None:
    client = get_redis()
    await client.delete(f"lock:{key}")


def build_cache_key(*parts: Any) -> str:
    """构造缓存键。"""
    return ":".join(str(part) for part in parts)


__all__ = [
    "acquire_lock",
    "build_cache_key",
    "cache_delete",
    "cache_get",
    "cache_set",
    "close_redis",
    "get_redis",
    "release_lock",
]
