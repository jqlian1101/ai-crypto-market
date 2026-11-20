"""飞书自定义机器人适配器（PRD 22.4.2）。

- 协议：POST Webhook，JSON 体；``interactive`` 卡片为首选，``text`` 为降级。
- 签名：开启签名校验时，``sign = base64(HMAC-SHA256(key=f"{ts}\\n{secret}", msg=b""))``。
- 成功判定：HTTP 2xx 且业务码 ``code == 0``。飞书非 0 业务码即失败，不能只看 HTTP 状态。
- 长度：卡片内容超过 ``MAX_PAYLOAD_BYTES`` 时截断而非丢弃。
- 错误日志中 Webhook URL 必须脱敏（只保留 token 前 4 位）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from aicrypto.config.settings import Settings

CHANNEL = "feishu"
MAX_PAYLOAD_BYTES = 30 * 1024
REQUEST_TIMEOUT_SECONDS = 10.0
TRUNCATED_SUFFIX = "\n…（内容过长已截断）"


class FeishuDeliveryError(Exception):
    """投递失败。``retryable`` 决定是否进入重试队列。"""

    def __init__(self, message: str, *, code: str | None, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class FeishuResult:
    response_code: str
    response_body: dict[str, Any]
    latency_ms: int
    payload_bytes: int


def feishu_sign(secret: str, timestamp: int | None = None) -> tuple[str, int]:
    """按 PRD 22.4.2 计算签名，返回 ``(sign, timestamp)``。"""
    ts = timestamp if timestamp is not None else int(time.time())
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8"), ts


def truncate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """超过 30 KB 时截断卡片正文，返回 ``(payload, 是否截断)``。"""
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(raw) <= MAX_PAYLOAD_BYTES:
        return payload, False

    # 仅截断可安全改写的文本字段：卡片中第一个 markdown 正文。
    # JSON 外层结构、转义与后缀都会影响最终字节数，无法用单一公式精确求解，
    # 因此迭代收缩正文直到整体满足上限，保证结果一定 ≤ MAX_PAYLOAD_BYTES。
    trimmed = json.loads(json.dumps(payload))
    targets = _iter_markdown_elements(trimmed)
    if not targets:
        # 无可截断的正文字段：无法安全改写，交由调用方记录失败，而不是静默超限发送。
        raise FeishuDeliveryError(
            "卡片超过 30 KB 且无可截断的正文字段", code="PAYLOAD_TOO_LARGE", retryable=False
        )

    target = targets[0]
    original = target["content"]
    limit = len(original.encode("utf-8"))
    while True:
        target["content"] = _cut_utf8(original, limit) + TRUNCATED_SUFFIX
        size = len(json.dumps(trimmed, ensure_ascii=False).encode("utf-8"))
        if size <= MAX_PAYLOAD_BYTES:
            break
        if limit == 0:
            raise FeishuDeliveryError(
                "卡片结构本身超过 30 KB，无法截断到上限", code="PAYLOAD_TOO_LARGE", retryable=False
            )
        limit = max(0, limit - (size - MAX_PAYLOAD_BYTES) - 16)
    return trimmed, True


def _iter_markdown_elements(payload: dict[str, Any]) -> list[dict[str, Any]]:
    card = payload.get("card") or {}
    out: list[dict[str, Any]] = []
    for element in card.get("elements", []):
        if element.get("tag") == "div" and isinstance(element.get("text"), dict):
            out.append(element["text"])
        elif element.get("tag") == "markdown":
            out.append(element)
    return out


def _cut_utf8(text: str, max_bytes: int) -> str:
    """按字节截断且不切断多字节字符。"""
    encoded = text.encode("utf-8")[:max_bytes]
    return encoded.decode("utf-8", errors="ignore")


def redact_webhook(url: str) -> str:
    """日志脱敏：只保留 token 前 4 位。"""
    if "/hook/" not in url:
        return "***"
    head, token = url.rsplit("/hook/", 1)
    return f"{head}/hook/{token[:4]}***"


def build_body(
    payload: dict[str, Any], *, secret: str | None, timestamp: int | None = None
) -> dict[str, Any]:
    body = dict(payload)
    if secret:
        sign, ts = feishu_sign(secret, timestamp)
        body["timestamp"] = str(ts)
        body["sign"] = sign
    return body


async def send(
    payload: dict[str, Any],
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> FeishuResult:
    """发送一条飞书消息。成功返回 ``FeishuResult``，失败抛 ``FeishuDeliveryError``。"""
    if settings.FEISHU_WEBHOOK_URL is None:
        raise FeishuDeliveryError("FEISHU_WEBHOOK_URL 未配置", code=None, retryable=False)

    url = settings.FEISHU_WEBHOOK_URL.get_secret_value()
    secret = settings.FEISHU_SECRET.get_secret_value() if settings.FEISHU_SECRET else None

    safe_payload, _ = truncate_payload(payload)
    body = build_body(safe_payload, secret=secret)
    payload_bytes = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
    started = time.perf_counter()
    try:
        try:
            resp = await http.post(url, json=body)
        except httpx.TimeoutException as exc:
            raise FeishuDeliveryError("飞书请求超时", code="TIMEOUT", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise FeishuDeliveryError(
                f"飞书网络错误: {type(exc).__name__}", code="NETWORK", retryable=True
            ) from exc
    finally:
        if owns_client:
            await http.aclose()

    latency_ms = int((time.perf_counter() - started) * 1000)

    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text[:500]}

    code = data.get("code") if isinstance(data, dict) else None

    if resp.status_code == 429 or resp.status_code >= 500:
        raise FeishuDeliveryError(
            f"飞书 HTTP {resp.status_code}", code=str(resp.status_code), retryable=True
        )
    if resp.status_code >= 400:
        raise FeishuDeliveryError(
            f"飞书 HTTP {resp.status_code}", code=str(resp.status_code), retryable=False
        )

    # PRD 22.4.2：非 0 业务码即失败，即使 HTTP 为 200。
    # PRD 未列出飞书具体业务码的可重试性，因此业务码失败统一按可重试处理，
    # 由重试次数上限（NOTIFY_MAX_RETRIES）兜底，耗尽后进入死信队列。
    if code != 0:
        raise FeishuDeliveryError(
            f"飞书业务码 {code}: {data.get('msg', '')}", code=str(code), retryable=True
        )

    return FeishuResult(
        response_code=str(code),
        response_body=data if isinstance(data, dict) else {},
        latency_ms=latency_ms,
        payload_bytes=payload_bytes,
    )


__all__ = [
    "CHANNEL",
    "MAX_PAYLOAD_BYTES",
    "FeishuDeliveryError",
    "FeishuResult",
    "build_body",
    "feishu_sign",
    "redact_webhook",
    "send",
    "truncate_payload",
]
