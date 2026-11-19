"""FastAPI 依赖（请求 ID、追踪 ID、配置、分页）。

31.1：请求头 ``X-Request-Id`` 缺失则服务端生成并回传；
``X-Trace-Id`` 跨异步任务传递。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Query, Request

from aicrypto.common.pagination import MAX_LIMIT, PageParams
from aicrypto.config.settings import Settings, get_settings

REQUEST_ID_HEADER = "X-Request-Id"
TRACE_ID_HEADER = "X-Trace-Id"


@dataclass(frozen=True, slots=True)
class RequestContext:
    """一次请求的上下文标识。"""

    request_id: str
    trace_id: str | None = None


def resolve_settings(request: Request) -> Settings:
    """解析当前生效的 :class:`Settings`。

    **优先取应用实例上的配置**（``app.state.settings``），只有应用未显式
    注入时才回退到进程级单例。

    原因：``create_app(settings)`` 允许注入一份独立配置（测试就是这么用的）。
    若依赖直接读全局单例，就会读到另一份配置 —— 例如测试注入
    ``AUTH_ENABLED=False``，而全局单例仍是 ``AUTH_ENABLED=True``，
    导致鉴权行为与被测应用不一致。
    """
    settings = getattr(request.app.state, "settings", None)
    if isinstance(settings, Settings):
        return settings
    return get_settings()


def get_request_id(request: Request) -> str:
    """取请求 ID：优先透传 ``X-Request-Id``，否则生成。

    **同一个请求内必须返回同一个值** —— 中间件要把它写进响应头，
    处理器又要把它写进响应体的 ``meta.request_id``。若这里每次调用
    都重新生成随机值，响应头和响应体就会对不上。

    因此：中间件会先把结果缓存到 ``request.state.request_id``，
    本函数优先读缓存。缓存不存在时（中间件之外的场景）才现场生成。
    """
    cached = getattr(request.state, "request_id", None)
    if cached:
        return str(cached)
    existing = request.headers.get(REQUEST_ID_HEADER)
    if existing:
        return existing
    return f"req_{uuid.uuid4().hex[:26]}"


def get_trace_id(request: Request) -> str | None:
    """取追踪 ID。"""
    cached = getattr(request.state, "trace_id", None)
    if cached is not None:
        return str(cached) if cached else None
    return request.headers.get(TRACE_ID_HEADER)


def get_request_context(request: Request) -> RequestContext:
    """依赖：取本次请求的上下文（request_id + trace_id）。"""
    return RequestContext(request_id=get_request_id(request), trace_id=get_trace_id(request))


def get_pagination(
    limit: Annotated[int | None, Query(ge=1, le=MAX_LIMIT, description="每页条数")] = None,
    offset: Annotated[int, Query(ge=0, description="偏移量")] = 0,
    cursor: Annotated[str | None, Query(description="不透明游标")] = None,
) -> PageParams:
    """分页入参依赖。"""
    return PageParams(limit=limit or 50, offset=offset, cursor=cursor)


__all__ = [
    "REQUEST_ID_HEADER",
    "TRACE_ID_HEADER",
    "RequestContext",
    "get_pagination",
    "get_request_context",
    "get_request_id",
    "get_trace_id",
    "resolve_settings",
]
