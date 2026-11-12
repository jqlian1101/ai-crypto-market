"""系统探针路由（PRD 第 31.7 节）。

- ``GET /health`` —— 存活探针（**不鉴权**）
- ``GET /ready``  —— 就绪探针（依赖检查，**不鉴权**）
- ``GET /metrics`` —— Prometheus 指标（**不鉴权**，仅内网）

31.8：这三个端点不鉴权，但必须只绑定本地（34.5）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import PlainTextResponse

from aicrypto.common.clock import now_iso
from aicrypto.common.responses import success_payload
from aicrypto.config.settings import get_settings
from aicrypto.infrastructure.metrics import render_latest

router = APIRouter()


@router.get("/health", summary="存活探针")
async def health() -> dict[str, Any]:
    """存活探针：进程活着即返回 200。"""
    return success_payload(
        {"status": "ok", "timestamp": now_iso()},
        request_id="health",
        generated_at=_utcnow(),
    )


@router.get("/ready", summary="就绪探针")
async def ready(response: Response) -> dict[str, Any]:
    """就绪探针：检查数据库与 Redis 是否可达。

    任一依赖不可用时返回 503（``COMMON_SERVICE_UNAVAILABLE``）。
    """
    checks: dict[str, str] = {}

    checks["database"] = await _check_database()
    checks["redis"] = await _check_redis()

    ready_all = all(status == "ok" for status in checks.values())
    if not ready_all:
        response.status_code = 503

    return success_payload(
        {"status": "ready" if ready_all else "not_ready", "checks": checks},
        request_id="ready",
        generated_at=_utcnow(),
    )


@router.get("/metrics", summary="Prometheus 指标", response_class=PlainTextResponse)
async def metrics() -> Response:
    """Prometheus 指标暴露。"""
    return Response(content=render_latest(), media_type="text/plain; version=0.0.4")


async def _check_database() -> str:
    try:
        from sqlalchemy import text

        from aicrypto.infrastructure.database.session import get_engine

        engine = get_engine(get_settings())
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001 - 探针不应抛出
        return "unavailable"


async def _check_redis() -> str:
    try:
        from aicrypto.infrastructure.cache import get_redis

        client = get_redis(get_settings())
        await client.ping()
        return "ok"
    except Exception:  # noqa: BLE001 - 探针不应抛出
        return "unavailable"


def _utcnow():
    from aicrypto.common.clock import now

    return now()
