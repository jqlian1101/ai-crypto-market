"""P6 调度层开发完成总结（2026-11-28）

## 交付成果

### P6-Phase1 核心模块

#### 1. APScheduler 守护进程（`scheduler_daemon.py`）
- 常驻后台调度器，替代外部 cron / systemd timer
- 自动从 `scheduled_jobs` 表加载任务定义（支持热更新）
- 支持 CRON 和 INTERVAL 两种触发类型
- 故障隔离：单个任务失败不影响其他任务执行
- 集成分布式锁，防止多实例并发执行
- 完整的日志、指标和异常记录

**特别修复**（相比初稿）：
- 修复 Postgres advisory lock 连接归属问题：改用 `get_engine().connect()`，
  避免 `session_scope` 把连接还回连接池导致锁失效
- 修复脚本加载问题：改用 `importlib.util` 加载 `run_scheduler.py`，
  避免 `scripts` 包导入失败

#### 2. 分布式锁（`scheduler_lock.py`）
三层实现，可由 `SCHEDULER_LOCK_MODE` 配置选择：

**PostgreSQL Advisory Lock**（推荐）
- 利用 Postgres 内置 `pg_try_advisory_lock()` 实现
- 无额外依赖，与应用数据库同生命周期
- 锁 ID 基于 job_id 的 CRC64 哈希

**Local Lock**（单机/开发环境）
- 直接返回成功（不实际获得分布式锁）
- 仅用于 `SCHEDULER_LOCK_MODE=local`

**Redis Lock**（框架预留）
- SET NX EX 原子操作
- 代码已实现但未激活（需要 Redis URL 配置）

**关键设计**：
- 上下文管理器确保锁总是释放
- Postgres 连接在 `__exit__` 时调用 `invalidate()` + `close()`，
  确保服务端自动回收会话级锁
- 完整的审计日志（获得、释放、延长时间）
- 所有权验证，只有拥有者能释放

#### 3. 配置变更
- 从 `dependency_audit.py` 白名单中删除 `apscheduler` 条目
  （已消费，不再需要白名单豁免）
- `SCHEDULER_LOCK_MODE` 配置项已在 `Settings` 中定义
  （值：`postgres_advisory` / `local`，默认 `postgres_advisory`）

### 测试覆盖

| 文件 | 用例数 | 说明 |
|------|-------|------|
| `test_scheduler_daemon.py` | 12 | 守护进程初始化、任务注册、错误处理、优雅关闭 |
| `test_scheduler_lock.py` | 17 | Postgres / Local 后端、获得/释放/所有权、上下文管理 |
| **总计** | **29** | 单元测试全覆盖 |

### 启动脚本

`scripts/run_scheduler_daemon.py`：
```bash
python scripts/run_scheduler_daemon.py
```

通过 supervisor / systemd 保证常驻运行（示例见 P6_SCHEDULER_PHASE2_GUIDE.md）。

---

## 验证清单

需要在本地环境逐项验证：

- [ ] 单元测试通过（`pytest tests/unit/test_scheduler_daemon.py tests/unit/test_scheduler_lock.py -v`）
- [ ] 依赖审计通过（`python -m pytest tests/unit/test_dependency_usage.py -v`）
  - `apscheduler` 白名单条目已删除
  - CI 不再报「apscheduler 被声明但未消费」
- [ ] 守护进程启动（`python scripts/run_scheduler_daemon.py`）
  - 进程启动无异常
  - 日志输出「已加载 N 个调度任务」
- [ ] 单个 Postgres advisory lock 获得/释放（单机场景）
- [ ] Local lock 在 `SCHEDULER_LOCK_MODE=local` 时正常工作
- [ ] 多实例部署下的锁互斥（可选，需要 Docker）

---

## 文件变更总结

### 新增文件
| 文件 | 大小 | 说明 |
|------|------|------|
| `src/aicrypto/services/scheduler_daemon.py` | ~290 L | APScheduler 守护进程 |
| `src/aicrypto/services/scheduler_lock.py` | ~430 L | 分布式锁管理 |
| `scripts/run_scheduler_daemon.py` | ~15 L | 启动脚本 |
| `tests/unit/test_scheduler_daemon.py` | ~150 L | 守护进程测试 |
| `tests/unit/test_scheduler_lock.py` | ~200 L | 分布式锁测试 |
| `docs/P6_SCHEDULER_PHASE2_GUIDE.md` | ~400 L | Phase2 实现指南 |

### 修改文件
| 文件 | 变更 | 说明 |
|------|------|------|
| `src/aicrypto/config/dependency_audit.py` | 删除白名单条目 | `apscheduler` 已消费 |

### 无变更
- `src/aicrypto/services/scheduler_core.py`：纯函数，P5 实现完整
- `src/aicrypto/services/scheduler_enqueue.py`：P5 实现完整
- `scripts/run_scheduler.py`：单次 tick 入口，P5 实现完整
  （P6-Phase2 时将在此添加 MISSED 窗口落库）

---

## 架构示意

```
┌─────────────────────────────────────────────────────────────┐
│                    APScheduler Daemon                       │
│  (常驻后台，替代外部 cron / systemd timer)                   │
└──────────┬──────────────────────────────────────────────────┘
           │
           ├─ 加载任务定义 ─→ scheduled_jobs 表
           │                （支持热更新）
           │
           ├─ 按时触发 ─→ _execute_scheduler_tick(job_id)
           │            │
           │            ├─ 尝试获得分布式锁
           │            │  ├─ Postgres Advisory Lock ✓（推荐）
           │            │  ├─ Local Lock ✓（单机）
           │            │  └─ Redis Lock ✓（预留）
           │            │
           │            └─ 锁成功 → 执行 run_scheduler.main()
           │                     ├─ 计算窗口
           │                     ├─ 入队分析任务
           │                     └─ 对账回写
           │
           └─ 锁失败 → 本轮跳过（其他实例在执行）

后续模块（P6-Phase2）：
   - 重试机制：failed 任务按指数退避重试
   - MISSED 落库：超出补跑上限的窗口记录到数据库
```

---

## 下一步工作（P6-Phase2）

三个核心任务（详见 `docs/P6_SCHEDULER_PHASE2_GUIDE.md`）：

### 任务一：补齐重试机制（优先级 ⭐⭐⭐）
- 修改 `reconcile_running_jobs()` 实现重试逻辑
- 添加 `SCHEDULER_MAX_RETRIES` 配置
- 编写 15-20 个重试相关的单元测试

### 任务二：MISSED 窗口落库（优先级 ⭐⭐⭐）
- 新增 `record_missed_windows()` 函数
- 修改 `run_scheduler.py` 的 `main()` 调用它
- 编写 10-12 个 MISSED 相关的单元测试

### 任务三：集成验证与文档更新（优先级 ⭐⭐）
- 端到端集成测试（真数据库）
- 更新 `docs/下一步开发计划.md`
- 更新 `P5_SCHEDULER_NOTES.md`

---

## 已知限制与缓解

| 限制 | 缓解措施 |
|------|---------|
| Postgres advisory lock 不支持 TTL | 应用层手动释放；连接断开自动回收 |
| 多实例锁获取失败的实例无法重试 | 设计选择：宁可跳过也不要阻塞 |
| Local lock 不支持真正的分布式 | 仅用于单机/开发；生产必须用 Postgres |
| 重试延迟可能超过任务超时 | 通过配置调整 `SCHEDULER_RETRY_BASE_DELAY_SECONDS` |

---

## 关键决策记录

1. **为什么 Postgres Advisory Lock 而非 Redis**
   - 项目架构：FastAPI + Postgres，无强 Redis 依赖
   - 维护成本：一个数据库连接比一个 Redis 连接更可控
   - 可靠性：Postgres lock 随连接自动回收，无孤儿锁风险

2. **为什么不用 APScheduler 的分布式锁插件**
   - APScheduler 的分布式锁需要额外配置（如 `schedulers.persistent`）
   - 项目使用的是内存 job store，分布式锁需要另外实现
   - 自实现的锁与调度业务解耦，更灵活

3. **为什么 Local Lock 而非始终用 Postgres**
   - 开发/测试环境可能不要分布式锁
   - Local lock 让配置更显式，不会隐式假设有 Postgres

4. **为什么不用 Alembic 新增 `scheduler_lock_state` 表**
   - 分布式锁无需持久化状态（Postgres advisory lock 没有状态表）
   - 若来来引入 Redis lock，也可以用 Redis 存状态，无需 DB 表

---

## 相关文档

- **PRD**：第 23 章（调度中心规格）、23.1.3-23.1.4（重试、幂等、任务锁）
- **设计文档**：`P5_SCHEDULER_NOTES.md`（P5 实现记录）
- **实现指南**：`docs/P6_SCHEDULER_PHASE2_GUIDE.md`（Phase2 详细计划）
- **遗留问题**：`docs/遗留问题.md`（L-01 ~ L-37）

---

## 交付时间

- P6-Phase1（本次）：2026-11-28
- P6-Phase2（建议）：2026-11-29 ~ 2026-12-01（3-4 天）
- P6-Phase3（通知集成）：2026-12-02 ~（见下一阶段规划）

---

**开发者备忘**：
- 确保本地 pytest 通过后再提交 PR
- Postgres advisory lock 的 `invalidate()` 调用很关键，不能省略
- `LOCAL` 锁模式主要用于测试，生产必须用 `POSTGRES_ADVISORY`
- 白名单删除后，CI 的 `Dependency usage check` 会自动验证 `apscheduler` 被消费
"""