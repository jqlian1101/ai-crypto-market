"""分页语义（PRD 第 31.3 章）。

规则要点：

1. 列表端点**必须**支持 ``cursor``；
2. ``cursor`` 是不透明字符串，客户端不得解析或构造；
3. ``has_more=false`` 时不再返回 ``next_cursor``；
4. 默认排序必须确定性（带唯一 tiebreaker）。
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
MAX_LIMIT_LARGE_TABLE = 1000


class PageParams(BaseModel):
    """分页入参。"""

    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)
    cursor: str | None = Field(default=None, description="不透明游标，客户端不得构造")


class PaginationMeta(BaseModel):
    """分页 meta（31.2）。"""

    limit: int
    offset: int
    total: int | None = Field(default=None, description="总数；深翻页时可不返回")
    has_more: bool
    next_cursor: str | None = Field(default=None)


class PaginatedResponse[T](BaseModel):
    """分页成功响应。"""

    data: list[T]
    meta: dict[str, Any]


def encode_cursor(payload: dict[str, Any]) -> str:
    """把排序键编码为不透明游标。

    使用 base64url 编码的 JSON；**不做加密**，仅保证客户端不依赖其内部结构。
    """
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """解码游标。非法游标抛 :class:`ValueError`，由上层转 ``COMMON_BAD_REQUEST``。"""
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"非法 cursor: {cursor!r}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"非法 cursor 载荷: {cursor!r}")
    return payload


@dataclass(slots=True)
class CursorKey:
    """确定性排序键的游标载荷。

    必须包含唯一 tiebreaker（如主键），否则分页结果不可复现（31.3 规则 4）。
    """

    values: dict[str, Any]

    def encode(self) -> str:
        return encode_cursor(self.values)

    @classmethod
    def decode(cls, cursor: str | None) -> CursorKey | None:
        if not cursor:
            return None
        return cls(values=decode_cursor(cursor))


def build_pagination_meta(
    *,
    limit: int,
    offset: int,
    total: int | None,
    has_more: bool,
    next_cursor: str | None,
) -> dict[str, Any]:
    """构造分页 meta。``has_more=false`` 时丢弃 ``next_cursor``。"""
    meta: dict[str, Any] = {
        "limit": limit,
        "offset": offset,
        "total": total,
        "has_more": has_more,
    }
    if has_more and next_cursor:
        meta["next_cursor"] = next_cursor
    return meta


def resolve_limit(
    requested: int | None,
    *,
    default: int = DEFAULT_LIMIT,
    max_limit: int = MAX_LIMIT,
) -> int:
    """收敛 limit 到合法区间。"""
    value = default if requested is None else requested
    return max(1, min(value, max_limit))


SortDirection = Literal["asc", "desc"]
