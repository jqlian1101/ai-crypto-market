"""Alembic 环境配置。

约定（36.8）：

- 迁移脚本入 ``migrations/``
- 命名 ``NNNN_<snake_case_description>.py``，编号递增
- autogenerate 后**必须人工审查**
- 每个迁移必须有 ``downgrade()``；CI 校验 upgrade/downgrade 往返
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from aicrypto.config.settings import get_settings

# 导入全部模型，使 autogenerate 能看到完整元数据
from aicrypto.infrastructure.database import models  # noqa: F401
from aicrypto.infrastructure.database.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_settings = get_settings()
_database_url = os.getenv("DATABASE_URL") or _settings.DATABASE_URL
config.set_main_option("sqlalchemy.url", _database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本，不连库。"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
