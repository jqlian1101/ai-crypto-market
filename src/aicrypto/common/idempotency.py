"""幂等键语义（PRD 第 31.4 章）。

约定：

- 幂等键头：``Idempotency-Key: <client-generated-uuid>``
- 触发类端点可由服务端派生键（如 ``{job}:{snapshot_id}:{trigger}``）
- 保留期 24 小时
- 重复请求返回**首次的完整响应**，HTTP ``200``（而非 ``201``），
  响应头带 ``Idempotent-Replayed: true``
- 同键不同请求体 → ``409 COMMON_IDEMPOTENCY_KEY_REUSED``
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replayed"
IDEMPOTENCY_TTL_SECONDS = 24 * 3600


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    """幂等键与其请求体指纹。"""

    key: str
    request_hash: str

    def matches(self, other_request_hash: str) -> bool:
        """同一键但请求体不同 → 冲突（31.4）。"""
        return self.request_hash == other_request_hash


def hash_request_body(body: bytes | str | None) -> str:
    """计算请求体指纹。``None`` 与空体视为同一。"""
    if body is None:
        payload = b""
    elif isinstance(body, str):
        payload = body.encode("utf-8")
    else:
        payload = body
    return hashlib.sha256(payload).hexdigest()


def derive_server_key(*parts: str) -> str:
    """由服务端派生幂等键。

    例：``derive_server_key("analysis_runs", snapshot_id, "manual")``
    """
    return ":".join(p for p in parts if p)


def validate_client_key(key: str | None) -> str | None:
    """校验客户端提供的幂等键格式。

    合法：长度 8–128 的可打印 ASCII（UUID、ULID、短哈希均可）。
    非法时抛 :class:`ValueError`，由上层转 ``COMMON_BAD_REQUEST``。
    """
    if key is None:
        return None
    stripped = key.strip()
    if not 8 <= len(stripped) <= 128:
        raise ValueError("Idempotency-Key 长度必须在 8–128 之间")
    if not stripped.isprintable() or " " in stripped:
        raise ValueError("Idempotency-Key 必须是无可空白字符的可打印字符串")
    return stripped
