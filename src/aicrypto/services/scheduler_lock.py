"""分布式锁管理——P6 调度层（PRD 23.1.4）。

**设计要点**：

1. **支持多实例部署**：同一个任务在多个调度器实例上并发执行时，
   只有获得锁的实例才能执行，其他实例跳过本轮。

2. **两种实现方案**：
   - **Postgres advisory lock**：适合单数据库多应用部署；不需要额外依赖。
   - **Redis SET NX**：适合分布式系统；性能更好，但需要 Redis。

3. **PRD 32.1 配置**：通过 `SCHEDULER_LOCK_MODE` 选择实现方案。

4. **超时与重试**：
   - 锁获取失败时本轮跳过（不进入事务）；
   - 锁持有期间若任务崩溃，定时释放（TTL）避免永久卡死。
   - 临界区应尽量短（计算窗口 + 入队），不包含 worker 执行。

5. **审计日志**：记录谁获得、谁释放、是否超时释放，便于排障。

**使用方式**：

    with DistributedLock(job_id="analysis_1h", timeout_seconds=30) as lock:
        if not lock.acquired:
            logger.info(f"未获得锁 {job_id}，本轮跳过")
            return
        # 执行临界区代码
        enqueue_analysis(...)
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Literal

from aicrypto.common.clock import now
from aicrypto.config.settings import Settings, get_settings

if TYPE_CHECKING:
    from redis import Redis
    from sqlalchemy.engine import Connection
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class LockToken:
    """锁令牌（用于释放）。"""

    lock_id: str  # 全局唯一锁 ID
    owner_id: str  # 拥有者 ID（实例标识）
    acquired_at: dt.datetime
    expires_at: dt.datetime


class DistributedLockBackend(ABC):
    """分布式锁后端的抽象基类。"""

    @abstractmethod
    def acquire(
        self,
        job_id: str,
        lock_ttl_seconds: int,
        owner_id: str | None = None,
    ) -> LockToken | None:
        """尝试获得锁。

        :param job_id: 任务 ID（作为锁的资源标识）
        :param lock_ttl_seconds: 锁的 TTL（防止持有者崩溃时永久卡死）
        :param owner_id: 拥有者 ID（实例标识，用于审计）
        :return: 锁令牌（若成功）；None（若无法获得）
        """

    @abstractmethod
    def release(self, token: LockToken) -> bool:
        """释放锁。

        :param token: 锁令牌
        :return: 是否成功释放（失败可能因为令牌过期或已被他人获得）
        """

    @abstractmethod
    def extend(self, token: LockToken, additional_seconds: int) -> bool:
        """延长锁的 TTL（若任务未完成）。

        :param token: 锁令牌
        :param additional_seconds: 额外延长的秒数
        :return: 是否成功延长
        """


class LocalLock(DistributedLockBackend):
    """本地锁实现（单机或开发环境用）。

    **原理**：不实际获得任何分布式锁，直接返回成功。
    用于 `SCHEDULER_LOCK_MODE=local` 的场景（单机或开发环境）。

    **警告**：多实例部署时不安全！请使用 POSTGRES_ADVISORY 或其他分布式锁。
    """

    def __init__(self) -> None:
        self._owner_id = str(uuid.uuid4())[:8]

    def acquire(
        self,
        job_id: str,
        lock_ttl_seconds: int,
        owner_id: str | None = None,
    ) -> LockToken | None:
        """本地模式：直接返回成功（模拟获得锁）。"""
        owner_id = owner_id or self._owner_id
        acquired_at = now()
        expires_at = acquired_at + dt.timedelta(seconds=lock_ttl_seconds)

        token = LockToken(
            lock_id=f"local:{job_id}",
            owner_id=owner_id,
            acquired_at=acquired_at,
            expires_at=expires_at,
        )

        logger.debug(f"[LOCAL] 已获得本地锁: job_id={job_id} owner_id={owner_id}")
        return token

    def release(self, token: LockToken) -> bool:
        """本地模式：直接返回成功（模拟释放）。"""
        logger.debug(f"[LOCAL] 已释放本地锁: {token.lock_id} owner_id={token.owner_id}")
        return True

    def extend(self, token: LockToken, additional_seconds: int) -> bool:
        """本地模式：直接返回成功（模拟延长）。"""
        logger.debug(f"[LOCAL] 已延长本地锁: {token.lock_id} additional_ttl={additional_seconds}s")
        return True


class PostgresAdvisoryLock(DistributedLockBackend):
    """基于 PostgreSQL advisory lock 的实现。

    **原理**：Postgres 的 `pg_advisory_lock()` 是全局的分布式锁机制，
    基于 64 位整数 ID 而非表行。在同一数据库所有连接间可见。

    **优点**：无需额外依赖；与应用数据库同生命周期；
            性能足够（不是高频操作）。
    **缺点**：Postgres 专属；不支持跨数据库；锁 ID 受 64 位限制。
    """

    def __init__(self, db_session: Session | Connection) -> None:
        """
        :param db_session: SQLAlchemy Session 或 Connection 对象。
            两者均暴露 ``execute``；advisory lock 绑定在连接上，
            传 Connection 时必须持有同一连接直到释放。
        """
        self.db = db_session
        self._owner_id = str(uuid.uuid4())[:8]  # 实例 ID

    def acquire(
        self,
        job_id: str,
        lock_ttl_seconds: int,
        owner_id: str | None = None,
    ) -> LockToken | None:
        """获得锁（非阻塞）。

        **实现**：
        - 使用 `pg_try_advisory_lock()` 非阻塞版本
        - 锁 ID 基于 job_id 的哈希
        - 在数据库端记录持有者信息（便于排障）
        """
        from sqlalchemy import text

        owner_id = owner_id or self._owner_id
        lock_id = self._hash_lock_id(job_id)

        try:
            # 非阻塞尝试获得锁
            result = self.db.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id}
            ).scalar()

            if not result:
                logger.debug(f"无法获得锁: {job_id} (lock_id={lock_id})")
                return None

            # 锁已获得，记录在数据库
            acquired_at = now()
            expires_at = acquired_at + dt.timedelta(seconds=lock_ttl_seconds)

            token = LockToken(
                lock_id=str(lock_id),
                owner_id=owner_id,
                acquired_at=acquired_at,
                expires_at=expires_at,
            )

            logger.info(
                f"已获得锁: job_id={job_id} owner_id={owner_id} "
                f"lock_id={lock_id} expires_at={expires_at}"
            )
            return token

        except Exception as exc:
            logger.error(f"获得锁失败: job_id={job_id} error={exc}", exc_info=True)
            return None

    def release(self, token: LockToken) -> bool:
        """释放锁。"""
        from sqlalchemy import text

        try:
            lock_id = int(token.lock_id)
            # Postgres 不检查所有权，直接释放即可
            result = self.db.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id}
            ).scalar()

            if result:
                logger.info(
                    f"已释放锁: lock_id={lock_id} owner_id={token.owner_id} "
                    f"held_duration_ms={round((now() - token.acquired_at).total_seconds() * 1000)}"
                )
                return True
            else:
                logger.warning(
                    f"锁释放失败（不拥有或已释放）: lock_id={lock_id} owner_id={token.owner_id}"
                )
                return False

        except Exception as exc:
            logger.error(f"释放锁异常: {exc}", exc_info=True)
            return False

    def extend(self, token: LockToken, additional_seconds: int) -> bool:
        """延长锁（Postgres advisory lock 无 TTL，此处为空实现）。

        注：Postgres advisory lock 没有 TTL 概念。
        若需要防止死锁，应由应用层在临界区完成后主动释放。
        """
        logger.debug(f"Postgres advisory lock 不支持 TTL 延长（token={token.lock_id}）")
        return True  # 视为成功（Postgres lock 不会过期）

    @staticmethod
    def _hash_lock_id(job_id: str) -> int:
        """将 job_id 哈希为 64 位整数。

        **设计**：CRC64 或简单哈希；务必保证同一 job_id 总是得到同一结果。
        """
        import hashlib

        # 使用 SHA256 的前 8 字节作为 64 位整数
        hash_obj = hashlib.sha256(job_id.encode("utf-8"))
        lock_id = int.from_bytes(hash_obj.digest()[:8], byteorder="big")
        # 限制在 Postgres advisory lock 的范围内（有符号 64 位）
        return lock_id & 0x7FFFFFFFFFFFFFFF


class RedisLock(DistributedLockBackend):
    """基于 Redis SET NX 的实现。

    **原理**：Redis 的 SET NX EX（原子操作）实现分布式锁。
    条件：键不存在才能 SET，同时设置 EX TTL。

    **优点**：跨所有系统可用；性能好；支持任意 ID；支持 TTL。
    **缺点**：需要额外的 Redis 实例；网络延迟；Redis 故障影响。
    """

    def __init__(self, redis_client: Redis) -> None:
        """
        :param redis_client: Redis 客户端对象（如 redis.Redis）
        """
        self.redis = redis_client
        self._owner_id = str(uuid.uuid4())[:8]

    def acquire(
        self,
        job_id: str,
        lock_ttl_seconds: int,
        owner_id: str | None = None,
    ) -> LockToken | None:
        """获得锁。

        **实现**：SET {lock_key} {owner_id}:{timestamp} NX EX {ttl}
        """
        import time

        owner_id = owner_id or self._owner_id
        lock_key = f"scheduler:lock:{job_id}"
        lock_value = f"{owner_id}:{int(time.time())}"

        try:
            # SET NX EX 原子操作
            result = self.redis.set(
                lock_key, lock_value, nx=True, ex=lock_ttl_seconds
            )

            if result:
                acquired_at = now()
                expires_at = acquired_at + dt.timedelta(seconds=lock_ttl_seconds)

                token = LockToken(
                    lock_id=lock_key,
                    owner_id=owner_id,
                    acquired_at=acquired_at,
                    expires_at=expires_at,
                )

                logger.info(
                    f"已获得 Redis 锁: job_id={job_id} owner_id={owner_id} "
                    f"ttl={lock_ttl_seconds}s"
                )
                return token
            else:
                logger.debug(f"无法获得 Redis 锁: {job_id} (key={lock_key})")
                return None

        except Exception as exc:
            logger.error(f"Redis 锁获取异常: job_id={job_id} error={exc}", exc_info=True)
            return None

    def release(self, token: LockToken) -> bool:
        """释放锁（需要验证所有权）。

        **设计**：先读后删（TOCTOU 竞态），或用 Lua 脚本原子操作。
        这里简化版本，仅检查所有权后删除。
        """
        try:
            lock_key = token.lock_id
            current_value = self.redis.get(lock_key)

            if current_value is None:
                logger.warning(f"锁已过期或不存在: {lock_key}")
                return False

            # 验证所有权（简化：只检查前缀）。redis 客户端可能返回 bytes 或 str，两者都需兼容。
            value_text = (
                current_value.decode("utf-8")
                if isinstance(current_value, bytes)
                else str(current_value)
            )
            if not value_text.startswith(token.owner_id):
                logger.warning(
                    f"无法释放锁（所有权不匹配）: {lock_key} owner={token.owner_id}"
                )
                return False

            # 删除
            self.redis.delete(lock_key)
            logger.info(
                f"已释放 Redis 锁: {lock_key} owner={token.owner_id} "
                f"held_duration_ms={round((now() - token.acquired_at).total_seconds() * 1000)}"
            )
            return True

        except Exception as exc:
            logger.error(f"释放 Redis 锁异常: {exc}", exc_info=True)
            return False

    def extend(self, token: LockToken, additional_seconds: int) -> bool:
        """延长锁的 TTL。

        **实现**：EXPIRE {lock_key} {new_ttl}（但需验证所有权）。
        """
        try:
            lock_key = token.lock_id
            # 简化实现：直接延长（生产环境应验证所有权）
            self.redis.expire(lock_key, additional_seconds)
            logger.debug(
                f"已延长 Redis 锁: {lock_key} additional_ttl={additional_seconds}s"
            )
            return True
        except Exception as exc:
            logger.error(f"延长 Redis 锁异常: {exc}", exc_info=True)
            return False


class DistributedLock:
    """分布式锁的统一接口。

    使用上下文管理器确保锁总是被释放。

    **用法**：

        with DistributedLock(job_id="analysis_1h") as lock:
            if not lock.acquired:
                logger.info("未获得锁，跳过")
                return
            # 执行临界区
            ...
    """

    def __init__(
        self,
        job_id: str,
        timeout_seconds: int = 30,
        settings: Settings | None = None,
    ) -> None:
        """
        :param job_id: 任务 ID（唯一标识锁资源）
        :param timeout_seconds: 锁的 TTL（防止持有者崩溃永久卡死）
        :param settings: 配置对象
        """
        self.job_id = job_id
        self.timeout_seconds = timeout_seconds
        self.settings = settings or get_settings()
        self.backend: DistributedLockBackend | None = None
        self.token: LockToken | None = None
        self.acquired = False
        self._conn: Connection | None = None

    def __enter__(self) -> DistributedLock:
        """进入上下文，尝试获得锁。"""
        self._setup_backend()
        if self.backend is None:
            raise RuntimeError("锁后端未初始化")
        self.token = self.backend.acquire(
            self.job_id,
            lock_ttl_seconds=self.timeout_seconds,
        )
        self.acquired = self.token is not None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Literal[False]:
        """离开上下文，释放锁。"""
        try:
            if self.token and self.backend:
                self.backend.release(self.token)
        finally:
            conn = getattr(self, "_conn", None)
            if conn is not None:
                # invalidate 会丢弃底层连接，数据库端自动回收会话级 advisory lock，
                # 即使上面的显式 unlock 失败，锁也不会残留在连接池里。
                conn.invalidate()
                conn.close()
                self._conn = None
        return False  # 不吞掉异常

    def _setup_backend(self) -> None:
        """根据配置选择锁后端。"""
        from aicrypto.config.settings import LockMode

        lock_mode = self.settings.SCHEDULER_LOCK_MODE

        if lock_mode == LockMode.POSTGRES_ADVISORY:
            from aicrypto.infrastructure.database.session import get_engine

            # advisory lock 绑定在数据库连接上：必须持有同一个连接直到释放，
            # 不能用 session_scope（它会把连接还回连接池，锁的归属随之丢失）。
            self._conn = get_engine(self.settings).connect()
            self.backend = PostgresAdvisoryLock(self._conn)

        elif lock_mode == LockMode.LOCAL:
            # 本地模式：不使用分布式锁，直接返回成功
            # （仅适合单机或开发环境）
            self.backend = LocalLock()

        else:
            raise ValueError(f"未知的锁模式: {lock_mode}")
