"""业务 API 路由（``/api/v1``）。

PRD 第 31 章约定：

- 业务端点一律挂在 ``/api/v1`` 前缀下；
- 探针端点（``/health`` ``/ready`` ``/metrics``）在根路径，**不鉴权**；
- 业务端点默认**需要鉴权**（34.3），由 ``require_auth`` 依赖强制；
- 统一响应封装（31.2）与稳定错误码（31.6）由全局中间件/异常处理器兜底。
"""

from __future__ import annotations

from fastapi import APIRouter

from aicrypto.api.routers.v1 import analysis, market, system

#: ``/api/v1`` 路由聚合器
api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(system.router)
api_v1_router.include_router(market.router)
api_v1_router.include_router(analysis.router)

__all__ = ["api_v1_router"]
