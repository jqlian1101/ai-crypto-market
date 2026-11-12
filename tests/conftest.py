"""pytest 共享 fixtures（PRD 第 36.2 节）。"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal

import pytest

from aicrypto.common.clock import FrozenClock, reset_clock, set_clock
from aicrypto.config.settings import Settings, reset_settings


@pytest.fixture
def frozen_clock() -> Iterator[FrozenClock]:
    """固定时钟。涉及时间的功能必须使用注入的时钟（36.1.3）。"""
    clock = FrozenClock(dt.datetime(2026, 11, 12, 0, 0, 0, tzinfo=dt.timezone.utc))
    set_clock(clock)
    yield clock
    reset_clock()


@pytest.fixture
def settings() -> Settings:
    """最小可用的 Settings。"""
    reset_settings()
    return Settings(
        DATABASE_URL="postgresql+psycopg://user:pass@localhost:5432/aicrypto",
        REDIS_URL="redis://localhost:6379/0",
        AUTH_ENABLED=False,
    )


@pytest.fixture
def decimal() -> Decimal:
    return Decimal


@pytest.fixture(autouse=True)
def _reset_global_state() -> Iterator[None]:
    """每个测试后重置全局单例，避免测试间串味。"""
    yield
    reset_settings()
    reset_clock()
