# AI Crypto Market Intelligence & Analysis Platform

个人自用的加密市场智能与分析平台。后端 FastAPI + PostgreSQL + Redis，前端 Next.js Dashboard。
完整需求与验收标准见 [`PRD_TDD.md`](PRD_TDD.md)。

当前状态：**Phase 0 底座 + Phase 1 行情采集 + Phase 2 衍生品采集已就绪** ——
配置体系、领域模型、错误码字典、指标埋点、统一响应契约、数据库迁移、
前端 Dashboard 框架，以及 K 线 / 持仓量 / 资金费率 / 盘口 / 聚合成交的采集链路
均可运行。AI 分析、异动推送、评估追踪按 PRD 第 20 章的 MVP 定义分阶段落地。

> **采集进程**（两个，需分别启动）：
>
> | 进程 | 采集内容 | 表 |
> |---|---|---|
> | `scripts/run_market_collector.py` | K 线（WS）+ REST 对账补数 | `market_candles` |
> | `scripts/run_derivatives_collector.py` | 持仓量 / 资金费率 / 盘口（REST 轮询）+ 聚合成交 / 标记价格（WS） | `open_interest_history`、`funding_history`、`orderbook_snapshots`、`market_trades` |
>
> 持仓量没有 WebSocket 推送，只能 REST 轮询，因此**衍生品异动的检出延迟高于价格异动**
> （PRD 29.2 / 29.4.3）。

---

## 目录

- [架构](#架构)
- [环境要求](#环境要求)
- [快速启动](#快速启动)
- [日常命令](#日常命令)
- [目录结构](#目录结构)
- [配置体系](#配置体系)
- [API 约定](#api-约定)
- [故障排查](#故障排查)

> **文档索引**
>
> | 文档 | 内容 |
> |---|---|
> | [`docs/TODO.md`](docs/TODO.md) | 完整任务清单（P0–P10），按阶段组织 |
> | [`docs/遗留问题.md`](docs/遗留问题.md) | **已确认存在但未修复**的问题，按严重度分级 |
> | [`PRD_TDD.md`](PRD_TDD.md) | 产品与技术设计（口径真源） |
> | [`docs/脚本用法说明.md`](docs/脚本用法说明.md) | 各脚本的用法 |

---

## 架构

```text
                    ┌─────────────────────────┐
                    │  Next.js 15 Dashboard    │
                    │  apps/web (port 3000)    │
                    └───────────┬─────────────┘
                                │ 同源代理 /api → :8000
                                │ (不硬编码后端 URL，PRD 35.11)
                    ┌───────────▼─────────────┐
                    │  FastAPI Backend        │
                    │  src/aicrypto (port 8000)│
                    │  · 统一响应 / 错误码      │
                    │  · Bearer Token 鉴权     │
                    │  · 结构化日志 + 指标      │
                    └──────┬───────────┬──────┘
                           │           │
              ┌────────────▼───┐   ┌───▼────────────┐
              │  PostgreSQL 16 │   │  Redis 7      │
              │  行情/快照/审计 │   │  缓存/锁/队列  │
              └────────────────┘   └────────────────┘
```

设计要点：

| 决策 | 理由 |
|---|---|
| 后端同步 SQLAlchemy + `psycopg3` | 采集与评估是批处理语义，同步栈更简单 |
| 数据库存 UTC，配置用 `Asia/Shanghai` | 存储统一，展示按用户时区解释 |
| 统一响应 `{data, meta}` + 稳定错误码 | 前端不解析自由文本，只按 `code` 分支 |
| 鉴权用静态 Bearer Token | 单用户本地系统，不引入用户体系 |
| 时间一律走可注入时钟 | 保证测试可复现（PRD 36.1.3） |

---

## 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | >= 3.12 | 推荐 3.12（`.python-version`） |
| Node.js | >= 20 | 已验证 24.x |
| Docker Desktop | 任意近期版本 | 只用于跑 PostgreSQL / Redis |
| PostgreSQL | 16 | 可由 `docker compose` 提供 |
| Redis | 7 | 可由 `docker compose` 提供 |

> **网络提示**：本机直连 `pypi.org` 与 Docker Hub 会超时。
> pip 需走镜像（本仓库已配置 `mirrors.aliyun.com`），容器镜像已使用
> `docker.m.daocloud.io` 前缀。恢复官方源时删除对应前缀即可。

---

## 快速启动

### 1. 基础设施（PostgreSQL + Redis）

```powershell
docker compose up -d
docker compose ps          # 等待两个服务都是 healthy
```

### 2. 后端

```powershell
# 首次：创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 准备环境变量（首次）
copy .env.example .env
# 然后编辑 .env，至少填好 DATABASE_URL / REDIS_URL / AUTH_TOKEN

# 建表
.\.venv\Scripts\python.exe -m alembic -c migrations\alembic.ini upgrade head

# 启动
.\.venv\Scripts\python.exe -m uvicorn aicrypto.api.app:create_app --factory --reload --port 8000
```

校验后端已就绪：

```powershell
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready    # 会检查 DB 与 Redis
```

### 3. 前端

```powershell
cd apps\web
npm install
copy .env.example .env.local   # API_PROXY_TARGET 默认即 http://127.0.0.1:8000
npm run dev
```

打开 <http://localhost:3000>。前端通过 Next.js rewrites 把 `/api/**` 代理到后端，
**不需要**在前端配置后端地址。

### 4. 访问令牌

后端默认 `AUTH_ENABLED=true`。业务端点需要 Bearer Token：

1. 从 `.env` 读取 `AUTH_TOKEN`；
2. 打开 <http://localhost:3000/auth/verify> 粘贴保存（存 `sessionStorage`）；
3. 或直接带请求头：`Authorization: Bearer <token>`。

探针端点 `/health`、`/ready`、`/metrics` **不鉴权**。

---

## 日常命令

### 后端

| 目的 | 命令 |
|---|---|
| 启动（开发热重载） | `.\.venv\Scripts\python.exe -m uvicorn aicrypto.api.app:create_app --factory --reload --port 8000` |
| 单元测试 | `.\.venv\Scripts\python.exe -m pytest tests/unit -q` |
| 契约测试 | `.\.venv\Scripts\python.exe -m pytest tests/contract -q` |
| 全部测试 | `.\.venv\Scripts\python.exe -m pytest -q` |
| 覆盖率 | `.\.venv\Scripts\python.exe -m pytest --cov --cov-report=term-missing` |
| Lint | `.\.venv\Scripts\python.exe -m ruff check src tests` |
| 格式化 | `.\.venv\Scripts\python.exe -m ruff format src tests` |
| 类型检查 | `.\.venv\Scripts\python.exe -m mypy` |
| 数据库迁移 | `.\.venv\Scripts\python.exe -m alembic -c migrations\alembic.ini upgrade head` |
| 迁移回退一版 | `.\.venv\Scripts\python.exe -m alembic -c migrations\alembic.ini downgrade -1` |
| 生成新迁移 | `.\.venv\Scripts\python.exe -m alembic -c migrations\alembic.ini revision --autogenerate -m "描述"` |

### 采集进程

| 目的 | 命令 |
|---|---|
| 启动 K 线采集 | `.\.venv\Scripts\python.exe scripts\run_market_collector.py` |
| 启动衍生品与盘口采集 | `.\.venv\Scripts\python.exe scripts\run_derivatives_collector.py` |
| 只采永续 | `... scripts\run_market_collector.py --market-types USDM_FUTURES` |
| 行情补数 | `.\.venv\Scripts\python.exe scripts\backfill_market.py` |
| K 线校验 | `.\.venv\Scripts\python.exe scripts\verify_market_candles.py` |
| 异动规则回放 | `.\.venv\Scripts\python.exe scripts\replay_anomaly.py` |

### 前端

| 目的 | 命令 |
|---|---|
| 开发 | `npm run dev` |
| 构建 | `npm run build` |
| 启动（生产） | `npm run start` |
| 类型检查 | `npm run typecheck` |
| Lint | `npm run lint` |
| 单元测试 | `npm test` |
| E2E | `npm run e2e`（需先 `npm run e2e:install`） |
| 重新生成 API 类型 | `npm run generate:api`（先导出契约，见下） |

### 基础设施

| 目的 | 命令 |
|---|---|
| 启动 | `docker compose up -d` |
| 停止 | `docker compose down` |
| 停止并清数据 | `docker compose down -v` |
| 查看日志 | `docker compose logs -f postgres` |

---

## 目录结构

```text
AICryptoMarket/
├── apps/
│   └── web/                 # Next.js 15 Dashboard（App Router）
├── src/
│   └── aicrypto/            # 后端包
│       ├── api/             # 应用工厂、中间件、鉴权、路由、HTTP Schema
│       │   ├── routers/     # /api/v1/** 业务端点
│       │   └── schemas/     # HTTP 请求/响应契约（OpenAPI 真源）
│       ├── common/          # 错误码、响应、时钟、ID、分页、脱敏
│       ├── config/          # Settings（唯一真源）+ 配置目录校验
│       ├── domain/          # 纯领域模型（无基础设施依赖）
│       ├── services/        # 可由 API、任务与离线流程复用的应用用例
│       └── infrastructure/  # 数据库（含 ORM 模型）、Redis、日志、指标
├── config/
│   ├── base.toml            # 非敏感结构化配置（阈值、配额、cron）
│   └── catalog.toml         # 配置项目录（CI 校验与 Settings 一致）
├── migrations/              # Alembic
├── tests/
│   ├── unit/                # 纯函数单测
│   └── contract/            # API 契约测试
├── docs/
├── docker-compose.yml
└── .env.example
```

> 目录结构对齐 PRD 第 19 章的**职责划分**（路径不照搬 PRD：后端采用 `src/` 布局）。
> `agents/`、`prompts/`、`skills/`、`tools/` 等在对应业务落地时填充。
>
> **依赖方向**：`api` → `services` → `domain` ← `infrastructure`。
> `domain/` 不导入 SQLAlchemy 或 Redis；ORM 模型统一放在
> `infrastructure/database/models/`，由 `migrations/env.py` 显式注册。

---

### API 契约类型

后端响应模型位于 `src/aicrypto/api/schemas/`，是 OpenAPI 的真源。后端 API 契约变更后，在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe scripts\export_openapi.py
cd apps\web
npm run generate:api
```

生成结果位于 `apps/web/src/api/generated/`，前端稳定类型别名位于
`apps/web/src/api/schema.ts`；不要手工编辑生成文件。

## 配置体系

加载优先级（低 → 高）：

```text
代码默认值 → .env / .env.local → 环境变量 → 数据库中的用户偏好
```

**唯一真源**是 `src/aicrypto/config/settings.py` 的 `Settings` 模型。
任何新增配置项必须同时出现在：

1. `config/base.toml`（非敏感）或 `.env.example`（敏感）；
2. `config/catalog.toml`（配置目录，CI 校验）；
3. PRD 第 32.4 章对应小节；
4. `Settings` 模型。

`tests/unit/test_config_catalog.py` 会自动校验 1 / 2 / 4 三者一致。

**安全约束**：密钥、token、IP 白名单、dry-run 开关**只能**来自环境变量，
不允许数据库或 API 覆盖。配置校验失败会**阻止进程启动**（不降级、不带病运行）。

---

## API 约定

### 成功响应

```json
{
  "data": { "...": "..." },
  "meta": {
    "request_id": "req_xxxxxxxx",
    "trace_id": null,
    "generated_at": "2026-11-17T08:00:00Z"
  }
}
```

### 失败响应

```json
{
  "error": {
    "code": "COMMON_NOT_FOUND",
    "message": "资源不存在",
    "details": {},
    "retryable": false,
    "request_id": "req_xxxxxxxx"
  }
}
```

`code` 是**稳定机器可读错误码**（登记在 `src/aicrypto/common/errors.py` 的
`ERROR_CATALOG`），前端**只能**按 `code` 分支，禁止解析 `message`。

### 请求头

| 头 | 用途 |
|---|---|
| `Authorization: Bearer <token>` | 业务端点鉴权 |
| `X-Request-Id` | 请求 ID，缺失则服务端生成并回传 |
| `X-Trace-Id` | 跨异步任务的追踪 ID，透传 |
| `Idempotency-Key` | 触发类端点的幂等键（24h 保留） |
| `If-None-Match` / `If-Modified-Since` | 条件请求，配合 ETag |

### 端点

| 路径 | 鉴权 | 说明 |
|---|---|---|
| `GET /health` | 否 | 存活探针 |
| `GET /ready` | 否 | 就绪探针（检查 DB + Redis） |
| `GET /metrics` | 否 | Prometheus 指标 |
| `GET /api/v1/**` | 是 | 业务端点 |

---

## 故障排查

| 现象 | 原因与处理 |
|---|---|
| 启动报 `DATABASE_URL 缺失` | `.env` 未创建或未填该项。这是**设计行为**：校验失败必须阻止启动 |
| 启动报 `AUTH_TOKEN ... 必须配置` | `AUTH_ENABLED=true` 时必须给令牌；本地自用可临时设 `AUTH_ENABLED=false` |
| `/ready` 返回 503 | `docker compose ps` 看容器是否 healthy；确认 `.env` 里的端口与容器映射一致 |
| `alembic upgrade` 连不上库 | 用 `postgresql+psycopg://`（psycopg3），不是 `postgresql://`（psycopg2） |
| 前端报「后端服务不可用」 | 后端未启动，或 `apps/web/.env.local` 的 `API_PROXY_TARGET` 不对 |
| 前端 401 | 令牌未保存，去 `/auth/verify` 页粘贴 `.env` 里的 `AUTH_TOKEN` |
| pip 安装超时 | 走镜像：`pip install -i https://mirrors.aliyun.com/pypi/simple/` |
| docker pull 超时 | 镜像加 `docker.m.daocloud.io/library/` 前缀 |
| 前端 `tsc` 报类型错 | 先启动后端再跑 `npm run generate:api` 重新生成 `schema.d.ts` |

---

## 开发约定

- **时间**：一律用 `aicrypto.common.clock.now()`，禁止直接 `datetime.now()`。
- **错误**：用 `ERROR_CATALOG` 中已登记的错误码，禁止临时造码。
- **日志**：用 `structlog` 的 `get_logger()`，禁止 `print`；密钥自动脱敏。
- **数据库**：跨表写操作在**单个** `session_scope()` 内完成。
- **前端**：类型从 OpenAPI 生成（`npm run generate:api`），禁止手写；
  禁止 `any`；页面必须显式实现六种视图态。

---

## 许可证

MIT
