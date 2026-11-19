"""鉴权与访问控制（PRD 第 34 章）。

- 单用户本地系统，**不引入用户体系**，采用静态 Bearer Token（34.3.1）
- 令牌只通过 ``Authorization`` 头传递，**不放 URL、不放 Cookie**（34.3.1）
- 比较使用 **恒定时间比较**（``hmac.compare_digest``），防时序侧信道
- ``/health``、``/ready``、``/metrics`` **不鉴权**，但必须只绑定本地（31.8 / 34.5）
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from aicrypto.api.dependencies import resolve_settings
from aicrypto.common.errors import AuthenticationError, AuthorizationError
from aicrypto.config.settings import Settings, get_settings
from aicrypto.infrastructure.logging import get_logger
from aicrypto.infrastructure.metrics import auth_failures_total

logger = get_logger(__name__)

#: ``auto_error=False`` —— 由我们自己抛统一错误码，而不是 FastAPI 默认的 403
_bearer_scheme = HTTPBearer(auto_error=False)


def hash_token(token: str) -> str:
    """令牌 SHA-256 十六进制摘要。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, settings: Settings | None = None) -> bool:
    """校验令牌。

    支持两种模式：

    - ``AUTH_TOKEN`` 明文存储 → 恒定时间比较；
    - ``AUTH_TOKEN_SHA256`` 只存哈希 → 对哈希值做恒定时间比较。
    """
    cfg = settings or get_settings()
    if cfg.AUTH_TOKEN is not None:
        expected = cfg.AUTH_TOKEN.get_secret_value()
        return hmac.compare_digest(token, expected)
    if cfg.AUTH_TOKEN_SHA256:
        return hmac.compare_digest(hash_token(token), cfg.AUTH_TOKEN_SHA256.lower())
    return False


def client_ip(request: Request) -> str | None:
    """取客户端 IP。

    **不信任** ``X-Forwarded-For``：本项目默认直接暴露，不在反向代理之后。
    """
    host = request.client.host if request.client else None
    if host is None:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host


def check_ip_allowed(request: Request, settings: Settings | None = None) -> None:
    """来源 IP 白名单检查（34.3.3）。

    ``ALLOWED_IPS`` 未设置时不启用该检查。
    """
    cfg = settings or get_settings()
    allowed = cfg.allowed_ips
    if not allowed:
        return
    ip = client_ip(request)
    if ip is None:
        raise AuthorizationError("无法确定来源 IP")
    if ip not in allowed:
        logger.warning("ip_not_allowed", extra={"client_ip": ip})
        raise AuthorizationError("来源 IP 不在允许列表中")


async def require_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> str:
    """FastAPI 依赖：要求有效 Bearer Token。

    失败时抛 :class:`AuthenticationError`（``401 COMMON_UNAUTHORIZED``）。
    """
    cfg = resolve_settings(request)
    if not cfg.AUTH_ENABLED:
        return ""

    if credentials is None or not credentials.credentials:
        auth_failures_total.labels(reason="missing_token").inc()
        raise AuthenticationError("缺少 Authorization 头")

    if not verify_token(credentials.credentials, cfg):
        auth_failures_total.labels(reason="invalid_token").inc()
        raise AuthenticationError()

    check_ip_allowed(request, cfg)
    return credentials.credentials


def require_auth_if_enabled(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> str:
    """同步版本的鉴权依赖（非 async 上下文可用）。"""
    cfg = resolve_settings(request)
    if not cfg.AUTH_ENABLED:
        return ""
    if credentials is None or not credentials.credentials:
        auth_failures_total.labels(reason="missing_token").inc()
        raise AuthenticationError("缺少 Authorization 头")
    if not verify_token(credentials.credentials, cfg):
        auth_failures_total.labels(reason="invalid_token").inc()
        raise AuthenticationError()
    check_ip_allowed(request, cfg)
    return credentials.credentials


__all__ = [
    "check_ip_allowed",
    "client_ip",
    "hash_token",
    "require_auth",
    "require_auth_if_enabled",
    "verify_token",
]
