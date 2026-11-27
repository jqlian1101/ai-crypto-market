# AI Crypto Market Intelligence & Analysis Platform

个人自用的加密市场智能与分析平台。后端 FastAPI + PostgreSQL + Redis，前端 Next.js Dashboard。
完整需求与验收标准见 [`PRD_TDD.md`](PRD_TDD.md)。

当前状态：**Phase 0 底座 + Phase 1 行情采集 + Phase 2 衍生品采集 + Phase 2.5 异动检测已就绪** ——
配置体系、领域模型、错误码字典、指标埋点、统一响应契约、数据库迁移、
前端 Dashboard 框架，以及 K 线 / 持仓量 / 资金费率 / 盘口 / 聚合成交的采集链路
与衍生品异动规则族均可运行。AI 分析的 Prompt 版本化与成本计量已接通；
多 Agent 拆分、调度层与评估追踪按 PRD 第 20 章的 MVP 定义分阶段落地。

> **采集进程**（两个，需分别启动）：
>
> | 进程 | 采集内容 | 表 |
> |---|---|---|
> | `scripts/run_market_collector.py` | K 线（WS）+ REST 对账补数 | `market_candles` |
> | `scripts/run_derivatives_collector.py` | 持仓量 / 资金费率 / 盘口（REST 轮询）+ 聚合成交 / 标记价格（WS） | `open_interest_history`、`funding_history`、`orderbook_snapshots`、`market_trades` |
>
> 持仓量没有 WebSocket 推送，只能 REST 轮询，因此**衍生品异动的检出延迟高于价格异动**
> （PRD 29.2 / 29.4.3）。

### Prompt 与成本计量

分析 Prompt 已从代码外置到 `prompts/<agent>/<template>.v<n>.md`：

| 路径 | 说明 |
|---|---|
| `prompts/master/analysis.v1.md` | 系统提示（含 `{schema}` 占位符） |
| `prompts/master/analysis.user.v1.md` | 用户提示（含 `{snapshot}` 占位符） |

- **版本号在文件名里**，不写在内容里——避免「改了内容忘了改版本号」的漂移；
- 每次分析都会往 `model_runs` / `llm_cost_records` 写计量（token、延迟、费用），
  并登记 `prompt_versions`（含文件哈希，可校验磁盘与库内是否一致）；
- 报告的 `versions` 字段记录 `model_id` / `prompt_version` / `feature_version`。

**模型单价**来自 `config/base.toml` 的 `[llm.model_pricing]` 段，
需同步进 `model_pricing` 表才能参与计费：

```bash
python scripts/sync_model_pricing.py
```

调价时**必须递增 `--pricing-version`**——同版本号下单价变了会直接报错，
避免历史成本的版本号指向新价格。

**成本报表**（只读）汇总已发生的 token 与费用：

```bash
python scripts/report_llm_costs.py --days 30
```

按模型 / Agent / 档位 / Prompt 版本 / 调用状态 / 按日分组。
若报表提示「存在未定价调用」，其费用数字是**下界**而非实际支出——
先同步单价再解读。

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
| Python | >= 3.13 | 推荐 3.13（`.python-version`） |
| Node.js | >= 20 | 已验证 24.x |
| Docker Desktop | 任意近期版本 | 只用于跑 PostgreSQL / Redis |
| PostgreSQL | 16 | 可由 `docker compose` 提供 |
| Redis | 7 | 可由 `docker compose` 提供 |

> **网络提示**：本机直连 `pypi.org` 与 Docker Hub 会超时。
> pip 需走镜像（本仓库已配置 `mirrors.aliyun.com`），容器镜像已使用
> `docker.m.daocloud.io` 前缀。恢复官方源时删除对应前缀即可。

### macOS 本机环境（Apple Silicon）

已验证的本地组合：

| 项 | 版本 | 说明 |
|---|---|---|
| macOS | 26.6.2 (arm64) | Apple M4 / 10 核 / 16 GB |
| Python | 3.13 | 由 `uv` 提供，见下方命令 |
| Node.js | 24.12.0 | npm 走 `registry.npmmirror.com` |
| Docker | 29.3.1 + Compose v5.1.1 | 容器走 `docker.m.daocloud.io` |

> **端口约定**：本机 Homebrew 已运行 `postgresql@18`（5432）与 `redis`（6379），
> 且 PG 上承载其它项目的库，**不可停**。因此 `docker compose` 的宿主端口
> 改为 **15432 / 16379**（容器内仍是 5432 / 6379），与 CI 的 5432 / 6379 不同。
>
> **已知网络限制**：`fapi.binance.com` 与 `fstream.binance.com` 在本机超时，
> `api.binance.com` 返回 403。即 **futures WS 与现货 REST 无法在本机端到端验证**
> （见 `docs/遗留问题.md` L-08 / L-09），需换网络环境补验。

创建 Python 环境（`uv` 可离线复用已缓存的 3.13 解释器）：

```bash
# uv 装在 conda env 里；也可自行安装
export PATH="/opt/anaconda3/envs/agent/bin:$PATH"

uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e ".[dev]" \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

---

## 快速启动

### 1. 基础设施（PostgreSQL + Redis）

```bash
docker compose up -d
docker compose ps          # 等待两个服务都是 healthy
```

容器端口：PostgreSQL `127.0.0.1:15432`、Redis `127.0.0.1:16379`。

### 2. 后端

```bash
# 首次：创建虚拟环境并安装依赖
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e ".[dev]" \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 准备环境变量（首次）
cp .env.example .env
# 然后编辑 .env，至少填好 DATABASE_URL / REDIS_URL / AUTH_TOKEN

# 建表
.venv/bin/python -m alembic -c migrations/alembic.ini upgrade head

# 启动
.venv/bin/python -m uvicorn aicrypto.api.app:create_app --factory --reload --port 8000
```

> **``.env.example`` 可直接复制使用**：模板中未配置项按 PRD 32.2 留空，
> Settings 会把**可空字段**的空串归一化为 `None`（留空 = 未配置）；
> 必填字段留空仍然**阻止进程启动**（32.3）。
> 生成令牌：`.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))"`

校验后端已就绪：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready    # 会检查 DB 与 Redis
```

### 3. 前端

```bash
cd apps/web
npm install
cp .env.example .env.local   # API_PROXY_TARGET 默认即 http://127.0.0.1:8000
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

> 下表用 `python` 代表 venv 解释器。macOS/Linux 写 `.venv/bin/python`，
> Windows 写 `.\.venv\Scripts\python.exe`。也可以先 `source .venv/bin/activate`。

### 后端

| 目的 | 命令 |
|---|---|
| 启动（开发热重载） | `python -m uvicorn aicrypto.api.app:create_app --factory --reload --port 8000` |
| 单元测试 | `python -m pytest tests/unit -q` |
| 契约测试 | `python -m pytest tests/contract -q` |
| 全部测试 | `python -m pytest -q` |
| 覆盖率 | `python -m pytest --cov --cov-report=term-missing` |
| Lint | `python -m ruff check src tests` |
| 格式化 | `python -m ruff format src tests` |
| 类型检查 | `python -m mypy` |
| 数据库迁移 | `python -m alembic -c migrations/alembic.ini upgrade head` |
| 迁移回退一版 | `python -m alembic -c migrations/alembic.ini downgrade -1` |
| 生成新迁移 | `python -m alembic -c migrations/alembic.ini revision --autogenerate -m "描述"` |

> **集成测试需要数据库**：`tests/integration/` 下的测试通过
> `ANALYSIS_TEST_DATABASE_URL` / `ANOMALY_TEST_DATABASE_URL` 两个**进程环境变量**
> 定位测试库。
>
> `tests/integration/conftest.py` 会按以下优先级自动解析并注入这两个变量：
>
> | 优先级 | 来源 |
> |---|---|
> | 1 | 进程环境变量（CI 走这条——`ci.yml` 的 `env:` 段直接注入） |
> | 2 | 项目根目录 `.env` 的 `DATABASE_URL` |
> | 3 | 默认值 `postgresql+psycopg://aicrypto:aicrypto@127.0.0.1:5432/aicrypto` |
>
> 所以本地通常**不需要**手动设置——只要 `docker compose up -d` 起好了容器，
> 集成测试就会自动连上本机的 PG 16 并**真跑**（不再是 skip）。
>
> 数据库连不上时，测试**跳过**而不是报错（探活超时设了 2 秒，
> 不会像 psycopg 默认那样干等 140 秒）。
>
> > ⚠️ **不要用「在 `.env` 里配测试库」的做法**：
> > `.env` 由 pydantic-settings 读取，它**不会**把值写回 `os.environ`，
> > 而集成测试读的是 `os.getenv()`——配了也无效。
> > 上面第 2 条优先级由 `conftest.py` 主动解析 `.env` 文件绕过这一点。
>
> 另见 `docs/遗留问题.md` L-14 关于 `127.0.0.1` 与 `localhost` 的连接耗时差异。

### 采集进程

| 目的 | 命令 |
|---|---|
| 启动 K 线采集 | `python scripts/run_market_collector.py` |
| 启动衍生品与盘口采集 | `python scripts/run_derivatives_collector.py` |
| 只采永续 | `python scripts/run_market_collector.py --market-types USDM_FUTURES` |
| 行情补数 | `python scripts/backfill_market.py` |
| K 线校验 | `python scripts/verify_market_candles.py` |
| 异动规则回放 | `python scripts/replay_anomaly.py` |
| 同步模型单价到 `model_pricing` 表 | `python scripts/sync_model_pricing.py` |
| LLM 成本报表 | `python scripts/report_llm_costs.py` |

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
│   ├── base.toml            # 非敏感结构化配置（阈值、配额、cron、模型单价）
│   └── catalog.toml         # 配置项目录（CI 校验与 Settings 一致）
├── prompts/                # 分析 Prompt 模板（<agent>/<name>.v<n>.md）
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

```bash
python scripts/export_openapi.py
cd apps/web
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
| 启动报 `Input should be a valid boolean` | 可选字段在 `.env` 里留空即可；若是**必填**字段留空才是配置缺失 |
| `/ready` 返回 503 | `docker compose ps` 看容器是否 healthy；确认 `.env` 里的端口与容器映射一致 |
| `alembic upgrade` 连不上库 | 用 `postgresql+psycopg://`（psycopg3），不是 `postgresql://`（psycopg2） |
| 端口 5432/6379 被占用 | 本机 Homebrew 的 PG/Redis 已在跑；`docker compose` 用的是 **15432 / 16379** |
| `alembic upgrade` 报 `Operation not permitted` | 沙箱拦截了 TCP 连接；用有网络权限的方式执行 |
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
