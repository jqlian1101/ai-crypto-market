# P6-Phase2 实现计划 - 重试机制与 MISSED 窗口

**开始时间**：2026-11-28
**目标完成**：2026-11-30
**优先级**：⭐⭐⭐（高）

---

## 🎯 阶段目标

补齐调度层的两个关键功能：

1. **重试机制** — 失败任务按指数退避重试（已有纯函数，需接入调度路径）
2. **MISSED 窗口落库** — 超出补跑上限的计划时间记录到数据库（供日报展示）

---

## 📋 任务分解

### 任务 A：补齐重试机制

**现状**：
- ✅ P5 中已实现纯函数 `should_retry()` / `next_retry_at()`（见 `scheduler_core.py`）
- ❌ 这些函数**从未被消费**（调用方）
- 当前行为：任务失败 → 标记 `FAILED` → 结束，**无任何重试**

**目标**：
- 失败任务检查是否可重试
- 若可重试：生成重试窗口、入队分析任务
- 若不可重试：标记失败结束

**涉及文件**：
- `src/aicrypto/services/scheduler_reconcile_db.py` （修改）— reconcile_running_jobs() 添加重试逻辑
- `src/aicrypto/config/settings.py` （修改）— 新增 SCHEDULER_MAX_RETRIES 配置
- `tests/unit/test_scheduler_retry.py` （新增）— 15-20 个重试测试用例

**实现步骤**：

1. **添加配置项**（15 分钟）
   ```python
   # settings.py
   SCHEDULER_MAX_RETRIES: int = Field(
       default=2,
       description="调度任务失败时的最大重试次数（PRD 23.1.3）",
       ge=0, le=10
   )
   SCHEDULER_RETRY_BASE_DELAY_SECONDS: int = Field(
       default=30,
       description="重试的基础延迟（指数退避：base × 2^(attempt-1)）",
       ge=10, le=600
   )
   ```

2. **修改 reconcile_running_jobs()**（1 小时）
   - 当 `aggregate_job_status()` 返回 `FAILED` 时，调用 `should_retry()`
   - 若可重试：
     - 计算 `next_retry_at()`
     - 新增 `job_runs` 记录（`status=QUEUED`, `attempt=N+1`）
     - 为重试窗口重新入队分析任务
     - 原 job_run 保持 `FAILED` 状态
   - 若不可重试：标记 `FAILED` 结束

3. **编写单元测试**（45 分钟）
   ```python
   # test_scheduler_retry.py
   class TestRetryLogic:
       def test_should_retry_when_failed_and_attempts_available()
       def test_should_not_retry_when_max_attempts_exceeded()
       def test_first_retry_30s_delay()
       def test_second_retry_60s_delay()
       # ... 更多用例
   ```

4. **本地验证**（30 分钟）
   - 运行完整的单元测试
   - 验证重试次数计数正确
   - 验证延迟计算正确

**预期代码变更量**：
- `scheduler_reconcile_db.py`：+50-70 行
- `settings.py`：+10 行
- `test_scheduler_retry.py`：+200-300 行

---

### 任务 B：MISSED 窗口落库

**现状**：
- ✅ P5 中计算了 MISSED 窗口（超出补跑上限的计划时间）
- ❌ 只 print 到 stdout，**未落库**
- 结果：日报无法统计「有多少个分析窗口被错过」

**目标**：
- 将 MISSED 窗口记录到 `job_runs` 表（`status='MISSED'`）
- 供日报集成统计

**涉及文件**：
- `src/aicrypto/services/scheduler_reconcile_db.py` （修改）— 新增 record_missed_windows()
- `scripts/run_scheduler.py` （修改）— 调用 record_missed_windows()
- `tests/unit/test_scheduler_missed.py` （新增）— 10-12 个 MISSED 测试用例

**实现步骤**：

1. **实现 record_missed_windows() 函数**（30 分钟）
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
       # 1. 检查幂等键是否已存在（避免重复）
       # 2. 创建 JobRun 记录（status='MISSED'）
       # 3. 返回插入行数
   ```

2. **修改 run_scheduler.py 的 main()**（15 分钟）
   ```python
   # 当前代码（第 213-217 行）
   if missed_windows:
       print(f"scheduler_missed: job_id={job.job_id} windows={missed_windows}")
   
   # 改为
   missed_recorded = record_missed_windows(db, job.job_id, missed_windows, moment)
   if missed_windows:
       logger.warning(f"调度器错过窗口: job_id={job.job_id} count={len(missed_windows)}")
   ```

3. **编写单元测试**（45 分钟）
   ```python
   # test_scheduler_missed.py
   class TestMissedWindows:
       def test_missed_windows_recorded_to_job_runs()
       def test_missed_status_set_correctly()
       def test_idempotency_key_prevents_duplicate_missed()
       def test_missed_job_run_has_correct_scheduled_at()
       # ... 更多用例
   ```

4. **本地验证**（30 分钟）
   - 运行完整的单元测试
   - 验证 MISSED 窗口正确入库
   - 验证幂等性（重复落库不会重复）

**预期代码变更量**：
- `scheduler_reconcile_db.py`：+40-50 行
- `run_scheduler.py`：+5 行
- `test_scheduler_missed.py`：+150-200 行

---

## 📊 时间分配

| 任务 | 预计 | 说明 |
|------|------|------|
| A.1 配置项 | 15 分钟 | 新增 SCHEDULER_MAX_RETRIES |
| A.2 实现重试逻辑 | 1 小时 | 修改 reconcile_running_jobs() |
| A.3 重试测试 | 45 分钟 | 15-20 个用例 |
| A.4 本地验证 | 30 分钟 | 运行测试，验证功能 |
| **小计 A** | **2.5 小时** | |
| B.1 MISSED 函数 | 30 分钟 | record_missed_windows() |
| B.2 修改 run_scheduler | 15 分钟 | 调用入口 |
| B.3 MISSED 测试 | 45 分钟 | 10-12 个用例 |
| B.4 本地验证 | 30 分钟 | 运行测试，验证功能 |
| **小计 B** | **2 小时** | |
| C. 文档更新 | 30 分钟 | 更新 P6 完成状态 |
| **总计** | **5 小时** | 预计 1 天完成 |

---

## 🛠️ 开发指南

### 重试机制实现模板

```python
def reconcile_running_jobs(db: Session, now: dt.datetime) -> dict:
    """修改后的对账逻辑（包含重试）。"""
    reconciled = 0
    retried_count = 0
    
    for job_run in db.query(JobRun).filter(JobRun.status == "RUNNING"):
        analysis_statuses = get_analysis_statuses(db, job_run.job_run_id)
        final_status = aggregate_job_status(analysis_statuses)
        
        if final_status == "FAILED":
            attempt = job_run.attempt or 1
            max_attempts = settings.SCHEDULER_MAX_RETRIES
            
            if should_retry(final_status, attempt, max_attempts):
                # 生成重试窗口
                retry_at = next_retry_at(
                    failed_at=job_run.started_at,
                    attempt=attempt,
                    base=dt.timedelta(seconds=settings.SCHEDULER_RETRY_BASE_DELAY_SECONDS)
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
                db.flush()
                
                # 重新入队分析任务
                for symbol in get_symbols():
                    for market_type in settings.market_types:
                        enqueue_analysis(
                            db,
                            symbol=symbol,
                            market_type=market_type.value,
                            job_id=job_run.job_id,
                            scheduled_at=retry_at,
                            job_run_id=new_job_run.job_run_id,
                        )
                
                retried_count += 1
                logger.info(f"已安排重试: {job_run.job_id} attempt={attempt+1}")
            
            # 原 job_run 标记为 FAILED
            job_run.status = "FAILED"
        
        elif final_status == "SUCCESS":
            job_run.status = "SUCCESS"
        elif final_status == "TIMEOUT":
            job_run.status = "TIMEOUT"
        
        reconciled += 1
    
    db.commit()
    return {"reconciled": reconciled, "retried": retried_count}
```

### MISSED 窗口落库模板

```python
def record_missed_windows(
    db: Session,
    job_id: str,
    missed_windows: list[dt.datetime],
    now: dt.datetime,
) -> int:
    """将 MISSED 窗口记录到 job_runs 表。"""
    from aicrypto.infrastructure.database.models.scheduler import JobRun
    from aicrypto.common.ids import new_job_run_id
    from aicrypto.services.scheduler_core import make_idempotency_key
    
    inserted = 0
    for scheduled_at in missed_windows:
        # 幂等键检查
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
            status="MISSED",
        )
        db.add(job_run)
        inserted += 1
    
    db.flush()
    return inserted
```

---

## ✅ 验收标准

### 重试机制
- [ ] should_retry() / next_retry_at() 被 reconcile_running_jobs() 调用
- [ ] 失败任务按指数退避重试（30s → 60s → ...）
- [ ] 重试次数不超过配置上限
- [ ] 重试历史记录完整（attempt 字段递增）
- [ ] 15-20 个单元测试 100% 通过

### MISSED 窗口
- [ ] 超出补跑上限的窗口被标记为 MISSED
- [ ] MISSED 记录正确落库到 job_runs 表
- [ ] 幂等性：重复调用不会重复插入
- [ ] 10-12 个单元测试 100% 通过

### 整体
- [ ] 所有测试 100% 通过
- [ ] 依赖审计通过
- [ ] 文档已更新
- [ ] 生产就绪

---

## 📚 参考资源

- **PRD**：23.1.3（重试语义）、23.1.4（幂等与任务锁）
- **现有实现**：
  - `scheduler_core.py::should_retry()` — 判定是否重试
  - `scheduler_core.py::next_retry_at()` — 计算下次重试时间
  - `scheduler_enqueue.py::enqueue_analysis()` — 入队分析任务
  - `run_scheduler.py::main()` — 调度主逻辑

---

## 🚀 开始实现

**下一步**：
1. 在 `settings.py` 添加 SCHEDULER_MAX_RETRIES 和 SCHEDULER_RETRY_BASE_DELAY_SECONDS
2. 修改 `scheduler_reconcile_db.py` 的 reconcile_running_jobs()
3. 编写重试相关测试
4. 修改 `run_scheduler.py` 调用 record_missed_windows()
5. 编写 MISSED 相关测试
6. 本地验证所有测试通过
7. 更新文档
8. 提交 commit

**预计完成**：2026-11-30
