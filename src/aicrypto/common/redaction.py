"""日志脱敏工具（PRD 第 32.6 章）。

规则：全部敏感值在日志中掩码；错误堆栈中的 URL 也要掩码，
避免泄漏 ``https://open.feishu.cn/.../hook/...``、``corpsecret=...``、``key=...``。
"""

from __future__ import annotations

import re

#: 需要整体替换的敏感键
_SENSITIVE_KEYS = (
    "webhook_url",
    "webhookurl",
    "secret",
    "corp_secret",
    "corpsecret",
    "api_key",
    "apikey",
    "token",
    "password",
    "passwd",
    "authorization",
    "private_key",
    "access_key",
    "signature",
)

_KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(_SENSITIVE_KEYS) + r")(\s*[=:]\s*)(\"?)([^\s\"'&,;)]+)(\3)"
)

_URL_PATTERN = re.compile(r"(https?://)([^\s/?#]+)(/[^\s?#]*)")


def mask_value(value: str | None, *, keep: int = 4) -> str:
    """保留首尾各 ``keep`` 个字符，中间用 ``****`` 遮盖。"""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "****"
    return f"{value[:keep]}****{value[-keep:]}"


def mask_url(url: str) -> str:
    """掩码 URL 中的路径段（如飞书 webhook token）。"""

    def _replace(match: re.Match[str]) -> str:
        scheme, host, path = match.groups()
        if not path or path == "/":
            return match.group(0)
        segments = path.rsplit("/", 1)
        tail = mask_value(segments[-1]) if len(segments) > 1 else ""
        if len(segments) == 1:
            return f"{scheme}{host}{mask_value(path)}"
        return f"{scheme}{host}{segments[0]}/{tail}"

    return _URL_PATTERN.sub(_replace, url)


def redact(text: str) -> str:
    """对任意文本做敏感信息脱敏。"""
    if not text:
        return text
    masked = _KEY_VALUE_PATTERN.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}****{m.group(5)}",
        text,
    )
    return mask_url(masked)


def mask_config_value(key: str, value: object) -> str:
    """按配置键名判断是否敏感，返回脱敏后的展示值。

    分三种情形：

    1. **纯机密值**（token / secret / password / api_key 等）→ ``***``。
       这些值整体就是凭证，没有「保留部分」的意义。
    2. **URL 型凭证**（飞书/企微 webhook 等）→ :func:`mask_url`。
       保留 host 与路径前缀、只掩掉尾部的 token 段。
       这样运维在日志里仍能看出「是哪个 webhook」，又不会泄漏密钥本身。
    3. 其余 → 原样（经类型转换）。
    """
    lowered = key.lower()
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        # URL 型凭证优先走 URL 掩码：保留 host 便于排障
        return mask_url(value)
    if any(s in lowered for s in _SENSITIVE_KEYS):
        return "***"
    return str(value)
