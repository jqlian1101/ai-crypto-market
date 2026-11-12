"""FastAPI 依赖（请求 ID、追踪 ID、分页）。

31.1：请求头 ``X-Request-Id`` 缺失则服务端生成并回传；
``X-Trace-Id`` 跨异步任务传递。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Query, Request

from aicrypto.common.pagination import MAX_LIMIT, PageParams

REQUEST_ID_HEADER = "X-Request-Id"
TRACE_ID_HEADER = "X-Trace-Id"


def get_request_id(request: Request) -> str:
    """取请求 ID：优先透传 ``X-Request-Id``，否则生成。"""
    existing = request.headers.get(REQUEST_ID_HEADER)
    if existing:
        return existing
    return f"req_{uuid.uuid4().hex[:26]}"


def get_trace_id(request: Request) -> str | None:
    """取追踪 ID。"""
    return request.headers.get(TRACE_ID_HEADER)


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
    "get_pagination",
    "get_request_id",
    "get_trace_id",
]
