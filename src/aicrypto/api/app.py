"""应用工厂与中间件（PRD 第 31 章 / 第 34 章）。

职责：

- 统一响应封装（31.2）
- 统一错误码处理（31.6）
- 请求 ID / 追踪 ID 透传（31.1）
- 请求耗时与计数指标（37.1）
- 探针：``/health`` ``/ready`` ``/metrics``（31.7）
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from aicrypto.api.dependencies import REQUEST_ID_HEADER, TRACE_ID_HEADER, get_request_id
from aicrypto.common.errors import AICryptoError, ErrorCode
from aicrypto.config.settings import Settings, get_settings
from aicrypto.infrastructure.logging import (
    bind_context,
    clear_context,
    configure_logging,
    get_logger,
)
from aicrypto.infrastructure.metrics import (
    http_request_duration_ms,
    http_requests_total,
)

logger = get_logger(__name__)

#: 不需要记录请求日志的路径
_QUIET_PATHS = frozenset({"/health", "/ready", "/metrics"})


def _request_id_of(request: Request) -> str:
    existing = request.headers.get(REQUEST_ID_HEADER)
    return existing or get_request_id(request)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期。

    启动时：校验配置 → 打印脱敏摘要 → 初始化日志。
    关闭时：释放数据库引擎。
    """
    settings: Settings = app.state.settings
    configure_logging(
        level=settings.LOG_LEVEL,
        json_format=str(settings.LOG_FORMAT) == "json",
        sampling_rate=settings.LOG_SAMPLING_RATE,
    )
    logger.info(
        "app_starting",
        extra={
            "app_env": str(settings.APP_ENV),
            "config_hash": settings.config_hash(),
            "host": settings.APP_HOST,
            "port": settings.APP_PORT,
        },
    )
    # 34.2：发现疑似真实交易凭证 + dry-run 强制校验被关闭时拒绝启动
    _assert_no_live_trading_credentials(settings)

    yield

    from aicrypto.infrastructure.database import reset_engine

    reset_engine()
    logger.info("app_stopped")


def _assert_no_live_trading_credentials(settings: Settings) -> None:
    """真实交易凭证检查（34.2 / 7.7）。"""
    if settings.FREQTRADE_ENABLED and not settings.FREQTRADE_DRY_RUN_ENFORCE:
        from aicrypto.common.errors import ConfigurationError

        raise ConfigurationError(
            "检测到可能的真实交易凭证配置且 dry-run 强制校验被关闭，拒绝启动"
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建 FastAPI 应用。"""
    cfg = settings or get_settings()

    app = FastAPI(
        title="AI Crypto Market Intelligence & Analysis Platform",
        version="0.1.0",
        description="个人自用的加密市场智能与分析平台",
        lifespan=lifespan,
        docs_url="/docs" if str(cfg.APP_ENV) != "prod" else None,
        redoc_url=None,
    )
    app.state.settings = cfg

    _register_middleware(app)
    _register_exception_handlers(app)
    _register_routers(app)
    return app


def _register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """请求上下文：绑定 trace_id / request_id，统计耗时，回传请求 ID。"""
        request_id = _request_id_of(request)
        trace_id = request.headers.get(TRACE_ID_HEADER)
        bind_context(request_id=request_id, trace_id=trace_id)

        started = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            route = request.scope.get("route")
            route_path = getattr(route, "path", request.url.path)
            status = response.status_code if response is not None else 500
            http_request_duration_ms.labels(
                method=request.method, path=route_path
            ).observe(elapsed_ms)
            http_requests_total.labels(
                method=request.method, path=route_path, status=str(status)
            ).inc()
            if request.url.path not in _QUIET_PATHS:
                logger.info(
                    "http_request",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "status": status,
                        "duration_ms": round(elapsed_ms, 2),
                    },
                )
            clear_context()

        response.headers[REQUEST_ID_HEADER] = request_id
        if trace_id:
            response.headers[TRACE_ID_HEADER] = trace_id
        return response


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AICryptoError)
    async def handle_domain_error(request: Request, exc: AICryptoError) -> JSONResponse:
        """领域异常 → 31.2 的失败响应。"""
        request_id = _request_id_of(request)
        logger.warning(
            "domain_error",
            extra={"code": str(exc.code), "message": exc.message, "details": exc.details},
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=exc.to_payload(request_id),
            headers={REQUEST_ID_HEADER: request_id},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """字段级校验失败 → ``422 COMMON_VALIDATION_FAILED``。"""
        request_id = _request_id_of(request)
        errors = [
            {
                "field": ".".join(str(part) for part in error.get("loc", ())),
                "message": error.get("msg", ""),
                "type": error.get("type", ""),
            }
            for error in exc.errors()
        ]
        payload = {
            "error": {
                "code": str(ErrorCode.COMMON_VALIDATION_FAILED),
                "message": "字段级校验失败",
                "details": {"errors": errors},
                "retryable": False,
                "request_id": request_id,
            }
        }
        return JSONResponse(
            status_code=422, content=payload, headers={REQUEST_ID_HEADER: request_id}
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """HTTP 异常 → 统一错误格式。"""
        request_id = _request_id_of(request)
        code = {
            401: ErrorCode.COMMON_UNAUTHORIZED,
            403: ErrorCode.COMMON_FORBIDDEN,
            404: ErrorCode.COMMON_NOT_FOUND,
            429: ErrorCode.COMMON_RATE_LIMITED,
        }.get(exc.status_code, ErrorCode.COMMON_BAD_REQUEST)
        payload = {
            "error": {
                "code": str(code),
                "message": str(exc.detail),
                "details": {},
                "retryable": exc.status_code in (429, 503),
                "request_id": request_id,
            }
        }
        return JSONResponse(
            status_code=exc.status_code,
            content=payload,
            headers={REQUEST_ID_HEADER: request_id},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """未预期错误 → ``500 COMMON_INTERNAL_ERROR``。"""
        request_id = _request_id_of(request)
        logger.exception("unhandled_error", extra={"path": request.url.path})
        payload = {
            "error": {
                "code": str(ErrorCode.COMMON_INTERNAL_ERROR),
                "message": "未预期错误",
                "details": {"trace_id": request_id},
                "retryable": True,
                "request_id": request_id,
            }
        }
        return JSONResponse(
            status_code=500, content=payload, headers={REQUEST_ID_HEADER: request_id}
        )


def _register_routers(app: FastAPI) -> None:
    from aicrypto.api.routers import system

    app.include_router(system.router, tags=["system"])


__all__ = ["create_app", "lifespan"]
