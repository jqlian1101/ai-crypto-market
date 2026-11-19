"""端到端冒烟脚本：验证鉴权与业务端点。

从项目自身的配置读取令牌（``get_settings()``），不硬编码、不打印令牌明文。
"""

from __future__ import annotations

import sys

import httpx

from aicrypto.config.settings import get_settings

BASE = "http://127.0.0.1:8000"


def show(label: str, resp: httpx.Response) -> None:
    body = resp.text
    if len(body) > 400:
        body = body[:400] + "..."
    print(f"[{label}] HTTP {resp.status_code} -> {body}")


def main() -> int:
    settings = get_settings()
    token = settings.AUTH_TOKEN.get_secret_value() if settings.AUTH_TOKEN else ""
    print(f"AUTH_ENABLED={settings.AUTH_ENABLED}, token configured={bool(token)}")

    with httpx.Client(base_url=BASE, timeout=10.0) as client:
        show("health", client.get("/health"))
        show("ready", client.get("/ready"))

        # 无令牌 -> 应 401
        show("v1/events (no token)", client.get("/api/v1/system/events"))

        # 错误令牌 -> 应 401
        show(
            "v1/events (bad token)",
            client.get("/api/v1/system/events", headers={"Authorization": "Bearer wrong"}),
        )

        # 正确令牌 -> 应 200
        headers = {"Authorization": f"Bearer {token}"}
        show("v1/events (ok token)", client.get("/api/v1/system/events", headers=headers))
        show(
            "v1/quality-events",
            client.get("/api/v1/system/quality-events", headers=headers),
        )
        show(
            "v1/market/overview",
            client.get("/api/v1/market/overview?symbol=BTCUSDT", headers=headers),
        )
        show(
            "v1/market/candles",
            client.get("/api/v1/market/candles?symbol=BTCUSDT&timeframe=1h", headers=headers),
        )
        show(
            "v1/market/candles (bad timeframe)",
            client.get("/api/v1/market/candles?symbol=BTCUSDT&timeframe=7m", headers=headers),
        )
        show("docs", client.get("/openapi.json"))
        metrics = client.get("/metrics")
        print(f"[metrics] HTTP {metrics.status_code}, {len(metrics.text)} bytes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
