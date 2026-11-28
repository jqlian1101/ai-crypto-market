# P5 调度层实现进度

## 本轮完成的工作

**1. 数据库迁移与 ORM**（`0008_scheduler_tables`）
- `scheduled_jobs`：任务定义表（cron / interval 表达式、依赖链、超时、推送策略）。
- `job_runs`：执行记录表（幂等键 `{job_id}:{scheduled_at}`、状态、是否补跑）。
- `job_locks`：分布式锁表（备用，主要用 Redis SET NX）。

**2. 调度核心模块**（`scheduler_core.py`）
- `make_idempotency_key` / `parse_idempotency_key`：任务级幂等键生成与反解。
- `make_run_idempotency_key`：运行级幂等键（包含 symbol 和 market_type）。
- `next_cron_times`：纯函数 cron 解析，支持常见模式（`*`, `*/N`, `a-b`, `a,b,c`）。
- `compute_backfill_windows`：补跑窗口计算，区分 BACKFILL / MISSED。

**3. 入队服务**（`scheduler_enqueue.py`）
- `enqueue_analysis`：复用 `create_run`，自动设置 `analysis_mode`（BACKFILL / LIVE）。

**4. 调度入口脚本**（`scripts/run_scheduler.py`）
- 单次 tick 执行，从硬编码初始任务清单计算待执行窗口。
- 对每个窗口记录 `job_runs` 一行（作为后续查询上次成功时间的游标）。
- 按 (symbol, market_type) 入队分析任务，处理补跑和 MISSED 情况。
- SAVEPOINT 隔离每个窗口，单窗口失败不影响其他窗口。

**5. API 扩展**
- `create_run` 新增 `analysis_mode` 参数（默认 `LIVE`）。

**6. 单元测试**
- `test_scheduler_core.py`：cron 解析、幂等键、补跑计算、字段匹配。
- `test_scheduler_enqueue.py`：入队幂等性（框架，待 ORM mock）。

## 关键设计决策

### 幂等键分层
- **任务级**：`{job_id}:{scheduled_at ISO8601}`（`job_runs.idempotency_key`）。
- **运行级**：任务级 + `|{symbol}|{market_type}`（`analysis_runs.idempotency_key`）。
- 一个任务窗口可入队多个分析（按标的与市场），但同一窗口重复触发不会重复执行（唯一约束）。

### 补跑机制
- 首次运行无历史记录时，仅取最近一个到期窗口，不回溯 24h 避免暴力入队。
- 窗口距当前超过 `max_backfill_window_seconds` 被标记为 MISSED，记录但不执行。

### Cron 实现
- 暂未引入 `apscheduler`；采用纯函数逐分钟迭代。
- 支持 `*`, `*/N`, `a-b`, `a,b,c`；不支持 `?` 或 `L`（后期扩展）。

### 初始任务清单
| 任务 ID | 触发 | 描述 |
|---|---|---|
| `analysis_1h` | `5 * * * *` | 每小时第 5 分钟 |
| `analysis_15m` | `*/15 * * * *` | 每 15 分钟 |
| `analysis_4h` | `7 */4 * * *` | 每 4 小时第 7 分钟 |
| `analysis_daily` | `0 8 * * *` | 每日 08:00 UTC |

## 未来优化与待办

1. **从 DB 读取任务定义**：当前硬编码，应改为从 `scheduled_jobs` 表读取。
2. **分布式锁实现**：补全 `JobLock` 表的 Postgres advisory lock / Redis 集成。
3. **APScheduler 集成**（可选）：当前纯函数不常驻，若改为守护进程则引入。
4. **监控与报告**：MISSED 窗口应进入日报；补跑进度跟踪。
5. **复杂 Cron**：支持 `?` (day-or-weekday 冲突)、`L` (最后一个)、`#` (第 N 个)。

## 文件清单

新增：
- `src/aicrypto/infrastructure/database/models/scheduler.py`
- `src/aicrypto/services/scheduler_core.py`
- `src/aicrypto/services/scheduler_enqueue.py`
- `scripts/run_scheduler.py`
- `tests/unit/test_scheduler_core.py`
- `tests/unit/test_scheduler_enqueue.py`
- `migrations/versions/0008_scheduler_tables.py`

修改：
- `src/aicrypto/infrastructure/database/models/__init__.py`（导入 `scheduler`）
- `src/aicrypto/services/analysis.py`（`create_run` 添加 `analysis_mode` 参数）

## 下一步

- **P5-Part2**：worker 对接与任务执行。
- **P6**：通知渠道集成（见 `NotificationChannel` 表）。
- **P7**：评估指标与追踪。
