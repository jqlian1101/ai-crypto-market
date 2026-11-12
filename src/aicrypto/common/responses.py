"""统一响应封装（PRD 第 31.2 章）。

成功::

    {"data": {...}, "meta": {"request_id": ..., "trace_id": ..., "generated_at": ...}}

分页成功见 :mod:`aicrypto.common.pagination`。

失败由 :class:`aicrypto.common.errors.AICryptoError` 的
:meth:`~aicrypto.common.errors.AICryptoError.to_payload` 产出。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ResponseMeta(BaseModel):
    """非分页响应的 meta。"""

    model_config = ConfigDict(extra="allow")

    request_id: str = Field(description="请求 ID，服务端生成或透传 X-Request-Id")
    trace_id: str | None = Field(default=None, description="追踪 ID，跨异步任务传递")
    generated_at: datetime = Field(description="响应生成时刻（UTC）")


class SuccessResponse(BaseModel, Generic[T]):
    """单资源成功响应。"""

    model_config = ConfigDict(extra="allow")

    data: T
    meta: ResponseMeta


class ErrorBody(BaseModel):
    """失败响应体（31.2）。"""

    code: str = Field(description="稳定机器可读错误码，见 31.6")
    message: str = Field(description="人类可读描述，不保证稳定，禁止用于程序判断")
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = Field(default=False, description="客户端是否可重试")
    request_id: str


class ErrorResponse(BaseModel):
    """失败响应封装。"""

    error: ErrorBody


def success_payload(
    data: Any,
    *,
    request_id: str,
    trace_id: str | None = None,
    generated_at: datetime,
) -> dict[str, Any]:
    """构造成功响应体。"""
    return {
        "data": data,
        "meta": {
            "request_id": request_id,
            "trace_id": trace_id,
            "generated_at": generated_at,
        },
    }
