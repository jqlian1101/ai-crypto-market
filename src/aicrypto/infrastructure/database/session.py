"""数据库引擎与会话管理。

约定（36.7）：

- 跨表写操作必须在**单个事务**内；
- 状态变更用「追加事件」而非覆盖（30.5）；
- session 必须可注入，测试可用事务性 session 替代。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from aicrypto.config.settings import Settings, get_settings
from aicrypto.infrastructure.logging import get_logger

logger = get_logger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def build_engine(settings: Settings | None = None) -> Engine:
    """创建 SQLAlchemy 引擎。

    使用同步驱动（psycopg3）。项目是模块化单体 + 本地部署，
    同步栈比 async 栈更简单，且采集与评估任务本就是批处理语义。
    """
    cfg = settings or get_settings()
    engine = create_engine(
        cfg.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_timezone(dbapi_connection: Any, _record: Any) -> None:
        """连接建立时把会话时区固定为 UTC（30.1：一律存 UTC）。"""
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET TIME ZONE 'UTC'")

    return engine


def get_engine(settings: Settings | None = None) -> Engine:
    """获取全局引擎单例。"""
    global _engine
    if _engine is None:
        _engine = build_engine(settings)
    return _engine


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """获取全局 session 工厂。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(settings),
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
    return _session_factory


def reset_engine() -> None:
    """仅测试使用：释放引擎与 session 工厂。"""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """事务性会话上下文。

    正常结束提交，异常回滚。跨表写操作应在**单个** ``session_scope`` 内完成。
    """
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个会话，使用当前应用注入的配置。"""
    from aicrypto.api.dependencies import resolve_settings

    with session_scope(resolve_settings(request)) as session:
        yield session
