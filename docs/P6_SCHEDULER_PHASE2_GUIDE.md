"""P6-Phase2 实现指南：补齐重试机制与 MISSED 窗口落库。

本文档为后续开发的详细规划，基于已完成的 P6-Phase1（APScheduler + 分布式锁 + 动态任务定义）。

更新时间：2026-11-28
"""

# P6-Phase2 核心任务

## 任务一：补齐重试调度机制

### 背景
- P5 中已实现纯函数 `should_retry()` / `next_retry_at()`（见 `scheduler_reconcile.py`）
- 但这些函数**从未被调用方消费**（L-未闭合缺陷清单中的一条）
- 当前行为：任务失败 → 标记 `FAILED` → 结束，无任何重试

### 需要改动的模块

#### 1. `src/aicrypto/services/scheduler_reconcile_db.py`
当前函数 `reconcile_running_jobs()` 的逻辑：
```
遍历 RUNNING 的 job_runs → 读取 analysis_runs 聚合状态 → 更新为 SUCCESS / FAILED / TIMEOUT
```

**改动内容**：
- 当 `aggregate_job_status()` 返回 `FAILED` 时，调用 `should_retry()` 判断是否可重试
- 若可重试：
  - 计算 `next_retry_at()`
  - 新增一条 `job_runs` 记录（`status=QUEUED`, `attempt=N+1`, `scheduled_at={next_retry_at}`）
  - 为该窗口重新入队分析任务（调用 `enqueue_analysis()`）
  - 原 job_run 保持 `FAILED` 状态（作为历史记录，不改）
- 若不可重试：标记 `FAILED` 且不再尝试

#### 2. `src/aicrypto/services/scheduler_core.py` 
新增函数：
```python
def max_retry_attempts(job_id: str, settings: Settings) -> int:
    """从配置读取某任务的最大重试次数。
    
    当前硬编码为 2（PRD 23.1.3），后期可从 scheduled_jobs.max_retries 读取。
    """
    return settings.SCHEDULER_MAX_RETRIES  # 添加到 Settings
```

#### 3. 数据库模型 `infrastructure/database/models/scheduler.py`
**无需改动**（现有模型已支持）：
- `job_runs.attempt` — 重试次数（1 = 首次，2 = 第一次重试）
- `job_runs.retry_scheduled_at` — 若非 NULL，表示这是重试窗口
- `job_runs.failed_at` — 记录失败时间（用于计算退避延迟）

### 伪代码（供开发时参考）

```python
def reconcile_running_jobs(db: Session, now: dt.datetime) -> dict:
    """修改后的对账逻辑（包含重试）。"""
    reconciled = 0
    timeout_count = 0
    retried_count = 0
    
    for job_run in db.query(JobRun).filter(JobRun.status == "RUNNING"):
        analysis_statuses = get_analysis_statuses(db, job_run.job_run_id)
        final_status = aggregate_job_status(analysis_statuses)
        
        if final_status == "FAILED":
            # 判断是否可重试
            attempt = job_run.attempt or 1
            max_attempts = get_max_retry_attempts(job_run.job_id)
            
            if should_retry(final_status, attempt, max_attempts):
                # 计算下次重试时间
                retry_at = next_retry_at(
                    failed_at=job_run.started_at,
                    attempt=attempt,
                    base=dt.timedelta(seconds=30)  # PRD 23.1.3
                )
                
                # 创建重试窗口
                new_job_run = JobRun(
                    job_id=job_run.job_id,
                    scheduled_at=retry_at,
                    attempt=attempt + 1,
                    status="QUEUED",
                    is_backfill=False,
                )
                db.add(new_job_run)
                db.flush()  # 获得 job_run_id
                
                # 为重试窗口入队分析任务
                for symbol in get_symbols():
                    for market_type in settings.market_types:
                        enqueue_analysis(
                            db,
                            symbol=symbol,
                            market_type=market_type.value,
                            job_id=job_run.job_id,
                            scheduled_at=retry_at,
                            job_run_id=new_job_run.job_run_id,
                            now=now,
                            is_backfill=False,
                        )
                
                retried_count += 1
                logger.info(f"已安排重试: {job_run.job_id} attempt={attempt+1} retry_at={retry_at}")
            
            # 原 job_run 标记为 FAILED（无论是否重试）
            job_run.status = "FAILED"
        
        elif final_status == "TIMEOUT":
            # 超时不重试，直接标记
            job_run.status = "TIMEOUT"
            timeout_count += 1
        
        elif final_status == "SUCCESS":
            job_run.status = "SUCCESS"
        
        reconciled += 1
    
    return {
        "reconciled_count": reconciled,
        "timeout_count": timeout_count,
        "retried_count": retried_count,
        "still_running": db.query(JobRun).filter(JobRun.status == "RUNNING").count(),
    }
```

### 测试用例

新增 `tests/unit/test_scheduler_retry.py`（建议 15-20 个用例）：

1. **基础重试判定**
   - `test_should_retry_when_failed_and_attempts_available` ✓
   - `test_should_not_retry_when_max_attempts_exceeded` ✓
   - `test_should_not_retry_when_timeout` ✗（超时不重试）

2. **重试延迟计算**
   - `test_first_retry_30s_delay` ✓
   - `test_second_retry_60s_delay` ✓（指数退避）
   - `test_retry_at_calculated_from_failure_time` ✓

3. **数据库写入**
   - `test_retry_job_run_created_with_correct_attempt` ✓
   - `test_analysis_runs_re_enqueued_for_retry_window` ✓
   - `test_original_job_run_marked_failed_after_retry_scheduled` ✓

4. **边界情况**
   - `test_retry_respects_max_backfill_window` ✓（重试窗口不超过回溯上限）
   - `test_timeout_takes_precedence_over_failed_for_retry` ✗（有 TIMEOUT 时不重试）

---

## 任务二：MISSED 窗口落库与日报集成

### 背景
- P5 中 MISSED 窗口只 print 到 stdout（见 `run_scheduler.py` 第 213-217 行）
- 未落库，日报无法统计「有多少个计划时间被错过」

### 需要改动的模块

#### 1. `src/aicrypto/services/scheduler_reconcile_db.py`
新增函数：
```python
def record_missed_windows(
    db: Session,
    job_id: str,
    missed_windows: list[dt.datetime],
    now: dt.datetime,
) -> int:
    """将 MISSED 窗口记录到 job_runs 表。
    
    :param missed_windows: 超出补跑上限的计划时间列表
    :return: 插入的行数
    """
    from aicrypto.infrastructure.database.models.scheduler import JobRun
    from aicrypto.common.ids import new_job_run_id
    from aicrypto.services.scheduler_core import make_idempotency_key
    
    inserted = 0
    for scheduled_at in missed_windows:
        # 检查幂等键是否已存在（避免重复插入）
        idem_key = make_idempotency_key(job_id, scheduled_at)
        existing = db.query(JobRun).filter(JobRun.idempotency_key == idem_key).first()
        
        if existing:
            logger.debug(f"MISSED 窗口已存在（幂等）: {idem_key}")
            continue
        
        job_run = JobRun(
            job_run_id=new_job_run_id(),
            job_id=job_id,
            idempotency_key=idem_key,
            scheduled_at=scheduled_at,
            started_at=now,
            status="MISSED",  # 关键：标记为 MISSED
            is_backfill=False,
        )
        db.add(job_run)
        inserted += 1
    
    db.flush()
    return inserted
```

#### 2. 修改 `scripts/run_scheduler.py` 的 `main()` 函数
**当前代码**（第 213-217 行）：
```python
missed_count += len(missed_windows)
if missed_windows:
    print(
        f"scheduler_missed: job_id={job.job_id} windows={missed_windows}",
        flush=True,
    )
```

**改为**：
```python
# 落库 MISSED 窗口
from aicrypto.services.scheduler_reconcile_db import record_missed_windows
missed_recorded = record_missed_windows(db, job.job_id, missed_windows, moment)
missed_count += missed_recorded

if missed_windows:
    logger.warning(
        f"调度器错过窗口: job_id={job.job_id} count={len(missed_windows)} "
        f"windows={missed_windows}"
    )
```

#### 3. 日报集成（可选，P7 之后）
新增 SQL 视图或存储过程：
```sql
-- 获取某天的 MISSED 统计
SELECT job_id, COUNT(*) as missed_count
FROM job_runs
WHERE status = 'MISSED'
  AND scheduled_at >= DATE_TRUNC('day', :date_start)
  AND scheduled_at < DATE_TRUNC('day', :date_start + INTERVAL '1 day')
GROUP BY job_id;
```

### 测试用例

新增 `tests/unit/test_scheduler_missed.py`（建议 10-12 个用例）：

1. **基础落库**
   - `test_missed_windows_recorded_to_job_runs` ✓
   - `test_missed_status_set_correctly` ✓
   - `test_idempotency_key_prevents_duplicate_missed` ✓

2. **数据完整性**
   - `test_missed_job_run_has_correct_scheduled_at` ✓
   - `test_missed_job_run_has_no_analysis_runs` ✓（MISSED 不入队分析）

3. **计数**
   - `test_record_missed_returns_count` ✓
   - `test_multiple_missed_windows_all_recorded` ✓

4. **与主流程集成**
   - `test_missed_and_enqueued_windows_separate` ✓（MISSED 不混入 enqueued）

---

# 实现步骤（建议顺序）

## 第 1 步：补齐重试机制（优先级高）
1. 在 `Settings` 中添加 `SCHEDULER_MAX_RETRIES` 配置
2. 修改 `reconcile_running_jobs()` 实现重试逻辑
3. 新增 `record_missed_windows()` 函数
4. 编写并通过重试相关的 15 个单元测试
5. 本地验证：手动触发失败的分析任务，观察重试是否生效

## 第 2 步：MISSED 窗口落库（优先级中）
1. 修改 `run_scheduler.py` 的 `main()` 调用 `record_missed_windows()`
2. 编写并通过 MISSED 相关的 12 个单元测试
3. 本地验证：创建超出补跑上限的场景，检查 job_runs 中是否有 MISSED 行

## 第 3 步：集成验证（优先级中）
1. 运行完整的集成测试（真数据库）
2. 验证重试和 MISSED 在实际调度过程中的表现
3. 检查日志输出的完整性

## 第 4 步：文档更新
1. 更新 `P5_SCHEDULER_NOTES.md` 添加 P6-Phase2 完成说明
2. 更新 `docs/下一步开发计划.md`（P5 升级为 P6）
3. 更新 `docs/遗留问题.md`（L-01 / L-02 任务标记为完成）

---

# 配置项新增

需要在 `Settings` 和 `base.toml` 中添加：

```python
# settings.py
SCHEDULER_MAX_RETRIES: int = Field(
    default=2,
    description="调度任务失败时的最大重试次数（PRD 23.1.3）",
    ge=0,
    le=10,
)

SCHEDULER_RETRY_BASE_DELAY_SECONDS: int = Field(
    default=30,
    description="重试的基础延迟（指数退避：base × 2^(attempt-1)）",
    ge=10,
    le=600,
)
```

```toml
# base.toml
[scheduler]
max_retries = 2
retry_base_delay_seconds = 30
```

---

# 预期效果

完成 P6-Phase2 后，调度系统将支持：

✅ **重试机制**
- 失败任务自动按指数退避重试
- 支持配置最大重试次数
- 重试历史完整记录在 `job_runs.attempt`

✅ **错过窗口追踪**
- 超出补跑上限的计划时间被标记为 MISSED
- 日报可统计「有多少个分析窗口被错过」
- 便于识别调度器故障或配置不当

✅ **完整的调度可观测性**
- 每个计划窗口的生命周期：QUEUED → RUNNING → SUCCESS/FAILED/TIMEOUT/MISSED
- 重试链条可追溯
- 成本、耗时、错误原因都有记录

---

# 相关文件变更清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/aicrypto/services/scheduler_reconcile_db.py` | 修改 | 新增重试逻辑 + MISSED 落库函数 |
| `src/aicrypto/config/settings.py` | 修改 | 新增 SCHEDULER_MAX_RETRIES 配置 |
| `scripts/run_scheduler.py` | 修改 | 调用 record_missed_windows() |
| `tests/unit/test_scheduler_retry.py` | 新增 | 15-20 个重试测试用例 |
| `tests/unit/test_scheduler_missed.py` | 新增 | 10-12 个 MISSED 测试用例 |
| `tests/integration/test_scheduler_retry_e2e.py` | 新增 | 端到端集成测试（真数据库） |
| `docs/下一步开发计划.md` | 修改 | 更新 P6-Phase2 完成状态 |
| `P5_SCHEDULER_NOTES.md` | 修改 | 新增 P6 实现记录 |

---

# 已知风险与缓解措施

| 风险 | 缓解措施 |
|------|--------|
| 重试导致成本上升 | 实施成本上限（PRD 24.3）；监控重试频率 |
| MISSED 窗口堆积 | 设置保留期（P7）；定期清理历史数据 |
| 重试延迟过长 | 通过配置调整 `retry_base_delay_seconds` |
| 锁超时导致重试错过 | 确保锁 TTL > 最大任务执行时间 |

---

# 参考资源

- **PRD**：23.1.3（重试语义）、23.1.4（幂等与任务锁）、27.3（数据保留）
- **已有实现**：`scheduler_core.py` 的 `should_retry()` / `next_retry_at()`
- **白名单**：`dependency_audit.py`（`apscheduler` 已消费，P5-Phase1 完成后可删）
