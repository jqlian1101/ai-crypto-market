"""API 契约测试（PRD 第 31 章 / 36.3 必测清单）。

覆盖：

- 无令牌访问 API 返回 401（34.3）
- 统一响应封装（31.2）
- 统一错误码字典（31.6）
- 探针端点不鉴权（31.8）

**不依赖真实数据库**：只测试不需要 DB 的端点与中间件行为。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrypto.api.app import create_app
from aicrypto.config.settings import Settings


@pytest.fixture
def client() -> TestClient:
    settings = Settings(
        DATABASE_URL="postgresql+psycopg://user:pass@localhost:5432/db",
        REDIS_URL="redis://localhost:6379/0",
        AUTH_ENABLED=False,
    )
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def authed_client() -> TestClient:
    settings = Settings(
        DATABASE_URL="postgresql+psycopg://user:pass@localhost:5432/db",
        REDIS_URL="redis://localhost:6379/0",
        AUTH_ENABLED=True,
        AUTH_TOKEN="test-token-0123456789abcdef",
    )
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


class TestProbes:
    """31.8：``/health``、``/ready``、``/metrics`` 不鉴权。"""

    def test_health_returns_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "ok"

    def test_metrics_returns_prometheus_text(self, client: TestClient) -> None:
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "market_data_latency_ms" in response.text


class TestUnifiedResponse:
    """31.2：统一响应封装。"""

    def test_success_has_data_and_meta(self, client: TestClient) -> None:
        payload = client.get("/health").json()
        assert "data" in payload
        assert "meta" in payload
        assert "request_id" in payload["meta"]
        assert "generated_at" in payload["meta"]

    def test_request_id_is_echoed(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Request-Id": "req_custom_123"})
        assert response.headers["X-Request-Id"] == "req_custom_123"
        assert response.json()["meta"]["request_id"] == "req_custom_123"

    def test_request_id_generated_when_missing(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.headers["X-Request-Id"].startswith("req_")

    def test_trace_id_is_echoed(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Trace-Id": "tr_abc"})
        assert response.headers["X-Trace-Id"] == "tr_abc"


class TestAuthentication:
    """34.3：Bearer Token 鉴权。"""

    def test_missing_token_returns_401(self, authed_client: TestClient) -> None:
        response = authed_client.get("/api/v1/system/events")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "COMMON_UNAUTHORIZED"

    def test_invalid_token_returns_401(self, authed_client: TestClient) -> None:
        response = authed_client.get(
            "/api/v1/system/events", headers={"Authorization": "Bearer wrong-token"}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "COMMON_UNAUTHORIZED"

    def test_error_payload_has_required_fields(self, authed_client: TestClient) -> None:
        payload = authed_client.get("/api/v1/system/events").json()
        error = payload["error"]
        assert set(error) == {"code", "message", "details", "retryable", "request_id"}

    def test_health_does_not_require_token(self, authed_client: TestClient) -> None:
        assert authed_client.get("/health").status_code == 200


@pytest.mark.parametrize("count", [1, 2, 3])
def test_event_page_boundary(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    from aicrypto.common.pagination import CursorKey
    from aicrypto.services import system

    rows = [
        {
            "id": str(index),
            "occurred_at": "2026-11-18T00:00:00Z",
            "severity": "INFO",
            "category": "test",
            "message": "test event",
            "context": {},
        }
        for index in range(count)
    ]
    monkeypatch.setattr(
        system,
        "list_system_events",
        lambda *args, **kwargs: (
            rows[:2],
            {
                "limit": 2,
                "offset": 0,
                "total": None,
                "has_more": count > 2,
                **(
                    {
                        "next_cursor": CursorKey(
                            {"occurred_at": rows[1]["occurred_at"], "id": rows[1]["id"]}
                        ).encode()
                    }
                    if count > 2
                    else {}
                ),
            },
        ),
    )
    response = client.get("/api/v1/system/events?limit=2", headers={"X-Request-Id": "req_page"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] == rows[:2]
    assert payload["meta"]["request_id"] == response.headers["X-Request-Id"] == "req_page"
    pagination = payload["meta"]["pagination"]
    assert pagination["has_more"] is (count > 2)
    if count > 2:
        key = CursorKey.decode(pagination["next_cursor"])
        assert key is not None
        assert key.values == {name: rows[1][name] for name in ("occurred_at", "id")}
    else:
        assert "next_cursor" not in pagination


class TestErrorFormat:
    """31.6：未知路由返回统一错误格式。"""

    def test_not_found_uses_error_envelope(self, client: TestClient) -> None:
        response = client.get("/api/v1/does-not-exist")
        assert response.status_code == 404
        assert "error" in response.json()
        assert response.json()["error"]["code"] == "COMMON_NOT_FOUND"
