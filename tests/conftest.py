"""pytest 共享 fixtures（PRD 第 36.2 节）。

**测试必须与环境隔离**：不能依赖开发者本机的 ``.env``。否则本机
``.env`` 里配了 ``DATABASE_URL``，那些断言「缺配置必须拒绝启动」的用例
就会因为读到了环境值而失效 —— 测试变成「测机器」而不是「测代码」。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal

import pytest

from aicrypto.common.clock import FrozenClock, reset_clock, set_clock
from aicrypto.config.settings import Settings, reset_settings


@pytest.fixture(autouse=True, scope="session")
def _isolated_env() -> Iterator[None]:
    """屏蔽进程环境变量与 ``.env`` 里的配置项。

    做法：

    1. **关掉 dotenv 文件读取** —— 把 ``Settings.model_config["env_file"]``
       临时置为 ``None``。否则开发者本机的 ``.env`` 会被读进来，那些断言
       「缺配置必须拒绝启动」的用例会因为读到了环境值而失效 ——
       测试变成「测机器」而不是「测代码」。
    2. **清掉同名环境变量** —— 避免 CI / shell 里预设的变量污染。

    不能用「把变量设成空串」的做法：空串对 ``bool`` 字段是非法值
    （``''`` 不是合法布尔），会引入一堆和被测逻辑无关的解析错误。

    注意只动「本项目自己的配置项」，不动 ``PATH`` 之类的系统变量。
    """
    import os

    from aicrypto.config.settings import Settings as SettingsModel

    managed = list(SettingsModel.model_fields)
    saved_env = {name: os.environ.pop(name, None) for name in managed}
    saved_env_file = SettingsModel.model_config.get("env_file")
    try:
        SettingsModel.model_config["env_file"] = None
        yield
    finally:
        SettingsModel.model_config["env_file"] = saved_env_file
        for name, value in saved_env.items():
            if value is not None:
                os.environ[name] = value


@pytest.fixture
def frozen_clock() -> Iterator[FrozenClock]:
    """固定时钟。涉及时间的功能必须使用注入的时钟（36.1.3）。"""
    clock = FrozenClock(dt.datetime(2026, 11, 12, 0, 0, 0, tzinfo=dt.UTC))
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
