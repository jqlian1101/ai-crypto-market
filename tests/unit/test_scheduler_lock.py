"""分布式锁测试。"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from aicrypto.services.scheduler_lock import (
    DistributedLock,
    LockToken,
    PostgresAdvisoryLock,
    RedisLock,
)


class TestPostgresAdvisoryLock:
    """Postgres advisory lock 实现测试。"""

    def test_acquire_lock_success(self):
        """成功获得锁时返回 LockToken。"""
        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.return_value = True

        lock_backend = PostgresAdvisoryLock(mock_db)
        token = lock_backend.acquire("test_job", lock_ttl_seconds=30)

        assert token is not None
        assert token.owner_id == lock_backend._owner_id
        assert token.lock_id is not None

    def test_acquire_lock_failure(self):
        """无法获得锁时返回 None。"""
        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.return_value = False

        lock_backend = PostgresAdvisoryLock(mock_db)
        token = lock_backend.acquire("test_job", lock_ttl_seconds=30)

        assert token is None

    def test_acquire_lock_with_custom_owner_id(self):
        """使用自定义 owner_id。"""
        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.return_value = True

        lock_backend = PostgresAdvisoryLock(mock_db)
        token = lock_backend.acquire(
            "test_job", lock_ttl_seconds=30, owner_id="custom_owner"
        )

        assert token.owner_id == "custom_owner"

    def test_release_lock_success(self):
        """成功释放锁时返回 True。"""
        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.return_value = True

        lock_backend = PostgresAdvisoryLock(mock_db)
        token = LockToken(
            lock_id="12345",
            owner_id="test_owner",
            acquired_at=dt.datetime.now(dt.UTC),
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30),
        )

        result = lock_backend.release(token)
        assert result is True

    def test_release_lock_failure(self):
        """无法释放锁时返回 False。"""
        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.return_value = False

        lock_backend = PostgresAdvisoryLock(mock_db)
        token = LockToken(
            lock_id="12345",
            owner_id="test_owner",
            acquired_at=dt.datetime.now(dt.UTC),
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30),
        )

        result = lock_backend.release(token)
        assert result is False

    def test_hash_lock_id_deterministic(self):
        """哈希结果应为确定的。"""
        hash1 = PostgresAdvisoryLock._hash_lock_id("test_job")
        hash2 = PostgresAdvisoryLock._hash_lock_id("test_job")

        assert hash1 == hash2

    def test_hash_lock_id_different_for_different_jobs(self):
        """不同 job_id 的哈希应不同。"""
        hash1 = PostgresAdvisoryLock._hash_lock_id("job_1")
        hash2 = PostgresAdvisoryLock._hash_lock_id("job_2")

        assert hash1 != hash2


class TestRedisLock:
    """Redis 锁实现测试。"""

    def test_acquire_redis_lock_success(self):
        """成功获得 Redis 锁。"""
        mock_redis = MagicMock()
        mock_redis.set.return_value = True

        lock_backend = RedisLock(mock_redis)
        token = lock_backend.acquire("test_job", lock_ttl_seconds=30)

        assert token is not None
        assert "scheduler:lock:test_job" in token.lock_id

    def test_acquire_redis_lock_failure(self):
        """无法获得 Redis 锁。"""
        mock_redis = MagicMock()
        mock_redis.set.return_value = False

        lock_backend = RedisLock(mock_redis)
        token = lock_backend.acquire("test_job", lock_ttl_seconds=30)

        assert token is None

    def test_release_redis_lock_success(self):
        """成功释放 Redis 锁。"""
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"test_owner:12345"
        mock_redis.delete.return_value = 1

        lock_backend = RedisLock(mock_redis)
        token = LockToken(
            lock_id="scheduler:lock:test_job",
            owner_id="test_owner",
            acquired_at=dt.datetime.now(dt.UTC),
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30),
        )

        result = lock_backend.release(token)
        assert result is True

    def test_release_redis_lock_owner_mismatch(self):
        """所有权不匹配时无法释放。"""
        mock_redis = MagicMock()
        mock_redis.get.return_value = b"other_owner:12345"

        lock_backend = RedisLock(mock_redis)
        token = LockToken(
            lock_id="scheduler:lock:test_job",
            owner_id="test_owner",
            acquired_at=dt.datetime.now(dt.UTC),
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30),
        )

        result = lock_backend.release(token)
        assert result is False

    def test_extend_redis_lock(self):
        """延长 Redis 锁的 TTL。"""
        mock_redis = MagicMock()
        mock_redis.expire.return_value = 1

        lock_backend = RedisLock(mock_redis)
        token = LockToken(
            lock_id="scheduler:lock:test_job",
            owner_id="test_owner",
            acquired_at=dt.datetime.now(dt.UTC),
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30),
        )

        result = lock_backend.extend(token, 60)
        assert result is True


class TestDistributedLock:
    """DistributedLock 统一接口测试。"""

    def test_lock_context_manager_acquire_success(self):
        """上下文管理器成功获得锁。"""
        from aicrypto.services.scheduler_lock import DistributedLock

        with patch.object(PostgresAdvisoryLock, "acquire") as mock_acquire:
            mock_token = LockToken(
                lock_id="12345",
                owner_id="test_owner",
                acquired_at=dt.datetime.now(dt.UTC),
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30),
            )
            mock_acquire.return_value = mock_token

            with patch("aicrypto.services.scheduler_lock.get_settings") as mock_settings_func:
                mock_settings_func.return_value.SCHEDULER_LOCK_MODE = "postgres_advisory"

                with patch(
                    "aicrypto.infrastructure.database.session.get_engine"
                ) as mock_engine:
                    mock_conn = MagicMock()
                    mock_engine.return_value.connect.return_value = mock_conn

                    with DistributedLock("test_job") as lock:
                        assert lock.acquired is True

    def test_lock_context_manager_acquire_failure(self):
        """上下文管理器获取失败。"""
        from aicrypto.services.scheduler_lock import DistributedLock

        with patch.object(PostgresAdvisoryLock, "acquire") as mock_acquire:
            mock_acquire.return_value = None

            with patch("aicrypto.services.scheduler_lock.get_settings") as mock_settings_func:
                mock_settings_func.return_value.SCHEDULER_LOCK_MODE = "postgres_advisory"

                with patch(
                    "aicrypto.infrastructure.database.session.get_engine"
                ) as mock_engine:
                    mock_conn = MagicMock()
                    mock_engine.return_value.connect.return_value = mock_conn

                    with DistributedLock("test_job") as lock:
                        assert lock.acquired is False

    def test_lock_cleanup_on_exception(self):
        """异常发生时锁仍被释放。"""
        from aicrypto.services.scheduler_lock import DistributedLock

        with patch("aicrypto.services.scheduler_lock.PostgresAdvisoryLock") as mock_lock_class:
            mock_backend = MagicMock()
            mock_token = LockToken(
                lock_id="12345",
                owner_id="test_owner",
                acquired_at=dt.datetime.now(dt.UTC),
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30),
            )
            mock_backend.acquire.return_value = mock_token
            mock_lock_class.return_value = mock_backend

            with patch("aicrypto.services.scheduler_lock.get_settings") as mock_settings_func:
                mock_settings_func.return_value.SCHEDULER_LOCK_MODE = "postgres_advisory"

                with patch(
                    "aicrypto.infrastructure.database.session.get_engine"
                ) as mock_engine:
                    mock_conn = MagicMock()
                    mock_engine.return_value.connect.return_value = mock_conn

                    try:
                        with DistributedLock("test_job") as lock:
                            raise ValueError("Test exception")
                    except ValueError:
                        pass

                    # 验证释放被调用
                    assert mock_backend.release.called
