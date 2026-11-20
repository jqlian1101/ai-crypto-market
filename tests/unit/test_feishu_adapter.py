"""飞书推送适配器单元测试（PRD 22.4.2 / 22.4.4）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest
from pydantic import SecretStr

from aicrypto.config.settings import Settings
from aicrypto.infrastructure.notifications import feishu

TEXT_PAYLOAD = {"msg_type": "text", "content": {"text": "x"}}


def test_feishu_sign_matches_official_algorithm() -> None:
    secret = "test-secret"
    ts = 1700000000
    expected = base64.b64encode(
        hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
    ).decode()

    sign, got_ts = feishu.feishu_sign(secret, timestamp=ts)

    assert sign == expected
    assert got_ts == ts


def test_build_body_adds_sign_only_when_secret_present() -> None:
    plain = feishu.build_body(TEXT_PAYLOAD, secret=None)
    signed = feishu.build_body(TEXT_PAYLOAD, secret="s", timestamp=1)

    assert "sign" not in plain
    assert "timestamp" not in plain
    assert signed["timestamp"] == "1"
    assert signed["sign"]


def test_truncate_payload_keeps_small_payload_intact() -> None:
    payload = {"msg_type": "interactive", "card": {"elements": []}}

    out, truncated = feishu.truncate_payload(payload)

    assert truncated is False
    assert out == payload


def test_truncate_payload_cuts_oversized_markdown_within_limit() -> None:
    big = "中" * 20000  # 约 60 KB，超过 30 KB
    payload = {
        "msg_type": "interactive",
        "card": {"elements": [{"tag": "div", "text": {"tag": "lark_md", "content": big}}]},
    }

    out, truncated = feishu.truncate_payload(payload)

    assert truncated is True
    content = out["card"]["elements"][0]["text"]["content"]
    assert len(json.dumps(out, ensure_ascii=False).encode("utf-8")) <= feishu.MAX_PAYLOAD_BYTES
    assert content.endswith(feishu.TRUNCATED_SUFFIX)
    # 不得切断多字节字符：重新编码解码应无异常
    content.encode("utf-8").decode("utf-8")


def test_redact_webhook_masks_token() -> None:
    url = "https://open.feishu.cn/open-apis/bot/v2/hook/01d16a34-a1a1-4737"

    redacted = feishu.redact_webhook(url)

    assert "01d16a34-a1a1-4737" not in redacted
    assert redacted.endswith("01d1***")


def _settings(secret: str | None = None) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_URL="postgresql+psycopg://user:pass@localhost:5432/db",
        REDIS_URL="redis://localhost:6379/0",
        AUTH_ENABLED=False,
        FEISHU_ENABLED=True,
        FEISHU_WEBHOOK_URL=SecretStr("https://open.feishu.cn/open-apis/bot/v2/hook/abc"),
        FEISHU_SECRET=SecretStr(secret) if secret else None,
    )


def _client(status: int, body: object) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_send_success_when_business_code_zero() -> None:
    async with _client(200, {"code": 0, "msg": "success"}) as client:
        result = await feishu.send(TEXT_PAYLOAD, settings=_settings(), client=client)

    assert result.response_code == "0"


async def test_http_200_with_nonzero_code_is_failure() -> None:
    """PRD 22.4.2：HTTP 200 但业务码非 0 必须判失败。"""
    async with _client(200, {"code": 19021, "msg": "sign match fail"}) as client:
        with pytest.raises(feishu.FeishuDeliveryError) as exc_info:
            await feishu.send(TEXT_PAYLOAD, settings=_settings(), client=client)

    assert exc_info.value.code == "19021"
    assert exc_info.value.retryable is True


async def test_http_429_is_retryable() -> None:
    async with _client(429, {}) as client:
        with pytest.raises(feishu.FeishuDeliveryError) as exc_info:
            await feishu.send(TEXT_PAYLOAD, settings=_settings(), client=client)

    assert exc_info.value.retryable is True


async def test_http_400_is_not_retryable() -> None:
    async with _client(400, {}) as client:
        with pytest.raises(feishu.FeishuDeliveryError) as exc_info:
            await feishu.send(TEXT_PAYLOAD, settings=_settings(), client=client)

    assert exc_info.value.retryable is False


async def test_send_includes_sign_when_secret_configured() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await feishu.send(TEXT_PAYLOAD, settings=_settings(secret="sec"), client=client)

    body = captured["body"]
    assert isinstance(body, dict)
    assert "sign" in body
    assert "timestamp" in body
