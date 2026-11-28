"""调度核心：cron 计算、幂等键生成、补跑判定（PRD 23.1）。

**设计要点**：

1. **幂等键** `{job_id}:{scheduled_at}` 由调度器在计算窗口时生成，
   对应「某个任务在某个时间的执行」。

2. **状态流转**：
   - `RUNNING` → 入队成功，worker 执行中；
   - `SUCCESS` / `FAILED` / `TIMEOUT` → 任务完成；
   - `SKIPPED_DEPENDENCY` → 依赖未满足，跳过；
   - `MISSED` → 超出补跑窗口，记录为错过；
   - `CANCELLED` → 人工或系统取消。

3. **补跑逻辑** (23.1.3)：
   - 若任务错过了计划时间（如关机期间），且距当前不超过
     `max_backfill_window_seconds`，则标记 `is_backfill=true` 补跑；
   - 否则标记 `status=MISSED` 记录，不实际执行。

4. **Cron 表达式**：采用标准 5 字段格式 (minute hour day month weekday)，
   统一按 UTC 解释（PRD 23.1.1）。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JobSchedule:
    """任务计划信息（从 scheduled_jobs 表读取）。"""

    job_id: str
    job_type: str  # CRON / INTERVAL / EVENT / MANUAL
    cron_expr: str | None
    interval_seconds: int | None
    timezone: str  # 仅作参考文档；内部统一 UTC 处理（23.1.1）
    max_backfill_window_seconds: int
    enabled: bool


@dataclass(frozen=True)
class ScheduledWindow:
    """单次调度窗口。"""

    job_id: str
    # 任务计划执行时间（UTC）
    scheduled_at: dt.datetime
    # 是否补跑
    is_backfill: bool


def make_idempotency_key(job_id: str, scheduled_at: dt.datetime) -> str:
    """生成任务级幂等键：``{job_id}:{scheduled_at ISO8601}``（PRD 23.1.3）。

    注意：这是**任务级**键。同一任务、同一计划时刻对多个标的（symbol）
    需要各自独立的分析，因此真正落库的分析运行键必须再带上标的，见
    :func:`make_run_idempotency_key`。
    """
    iso_time = scheduled_at.astimezone(dt.UTC).isoformat()
    return f"{job_id}:{iso_time}"


def make_run_idempotency_key(
    job_id: str, scheduled_at: dt.datetime, *, symbol: str, market_type: str
) -> str:
    """分析运行级幂等键：任务键 + 标的 + 市场类型。

    同一 ``job_id:scheduled_at`` 下，每个 (symbol, market_type) 各一条，
    既保证跨 symbol 不冲突，又保证同一窗口重复触发只落一条。
    """
    base = make_idempotency_key(job_id, scheduled_at)
    return f"{base}|{symbol.strip().upper()}|{market_type.strip().upper()}"


def parse_idempotency_key(key: str) -> tuple[str, dt.datetime]:
    """反解幂等键为 (job_id, scheduled_at)。

    假设 job_id 中不包含冒号；若包含需改用不同分隔符。
    """
    parts = key.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid idempotency key format: {key}")
    job_id, iso_time = parts
    scheduled_at = dt.datetime.fromisoformat(iso_time)
    return job_id, scheduled_at


def next_cron_times(
    cron_expr: str,
    *,
    after: dt.datetime,
    max_count: int = 10,
) -> list[dt.datetime]:
    """计算 cron 表达式的下一个 N 个触发时间。

    **实现说明**：本函数暂不引入 croniter / APScheduler，
    而是手工实现基础 5 字段 cron 解析，逐分钟迭代。
    
    cron 格式：``minute hour day month weekday``（标准 5 字段）
    - `*` = 任意值；
    - 数字 = 精确值；
    - `*/N` = 每 N 个单位；
    - `a-b` = 范围；
    - `a,b,c` = 列表。
    
    **限制**：本版本实现简化版，仅支持常见模式（无星期几复杂逻辑）。
    复杂 cron（含 ? / L / W）暂不支持。
    
    参数：
      cron_expr: "5 * * * *" (每小时第 5 分钟)
      after: 搜索起点（UTC）
      max_count: 最多返回多少个触发时间
      
    返回：排序的 datetime 列表（UTC，分钟精度）。
    """
    # 简易实现：逐分钟迭代，检查是否匹配 cron 模式
    times: list[dt.datetime] = []
    
    # 解析 5 字段
    fields = cron_expr.split()
    if len(fields) != 5:
        raise ValueError(f"Invalid cron format (expect 5 fields): {cron_expr}")
    
    minute_str, hour_str, day_str, month_str, weekday_str = fields
    
    # 迭代搜索（逐分钟）。weekday 按**当前候选时刻**计算，不得复用 after 的星期。
    # cron 星期：0=Sunday..6=Saturday；Python weekday()：0=Monday..6=Sunday。
    current = after.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    limit = after + dt.timedelta(days=366)  # 1 年内搜索

    while current < limit and len(times) < max_count:
        cron_wd = (current.weekday() + 1) % 7
        if (
            _matches_field(current.minute, minute_str, 0, 59)
            and _matches_field(current.hour, hour_str, 0, 23)
            and _matches_field(current.day, day_str, 1, 31)
            and _matches_field(current.month, month_str, 1, 12)
            and _matches_field(cron_wd, weekday_str, 0, 6)
        ):
            times.append(current)
        current += dt.timedelta(minutes=1)
    
    return times


def _matches_field(value: int, pattern: str, min_val: int, max_val: int) -> bool:
    """检查数值是否匹配 cron 字段模式。
    
    支持：*, N, */N, a-b, a,b,c
    """
    if pattern == "*":
        return True
    
    if pattern.startswith("*/"):
        step = int(pattern[2:])
        return (value - min_val) % step == 0
    
    if "-" in pattern:
        # 范围：a-b
        a, b = pattern.split("-")
        return int(a) <= value <= int(b)
    
    if "," in pattern:
        # 列表：a,b,c
        return any(value == int(v) for v in pattern.split(","))
    
    # 单个数字
    return value == int(pattern)


def compute_backfill_windows(
    schedule: JobSchedule,
    *,
    now: dt.datetime,
    last_success_at: dt.datetime | None = None,
) -> tuple[list[ScheduledWindow], list[str]]:
    """计算从上次成功执行到现在之间的缺失窗口。

    返回：
      (windows, missed_windows)
      
      windows: 应该执行的窗口列表（包含补跑与当前）
      missed_windows: 超出补跑上限而被记录为 MISSED 的窗口
    """
    if schedule.job_type != "CRON":
        # INTERVAL / EVENT / MANUAL 不涉及补跑
        return [], []
    
    if schedule.cron_expr is None:
        raise ValueError(f"CRON job {schedule.job_id} has no cron_expr")
    
    if last_success_at is None:
        # 无成功记录（首次运行 / 新任务）：不做历史回溯，只取最近一个已到期窗口。
        # 否则 analysis_15m 首次启动会一次性入队约 96 个窗口 × 标的 × 市场。
        search_from = now - dt.timedelta(days=1)
        recent = next_cron_times(schedule.cron_expr, after=search_from, max_count=100)
        due = [t for t in recent if t <= now]
        if not due:
            return [], []
        latest = due[-1]
        return [ScheduledWindow(job_id=schedule.job_id, scheduled_at=latest, is_backfill=False)], []

    search_from = last_success_at

    # 计算从 search_from 到 now 之间的所有应该执行的时间
    all_windows = next_cron_times(schedule.cron_expr, after=search_from, max_count=100)
    
    windows: list[ScheduledWindow] = []
    missed: list[str] = []

    for scheduled_at in all_windows:
        # 仅统计已到期的窗口（未来时刻不属于本 tick 的工作）
        if scheduled_at > now:
            break
        age_seconds = (now - scheduled_at).total_seconds()
        if age_seconds > schedule.max_backfill_window_seconds:
            # 超出补跑上限：记录为 MISSED，不执行
            missed.append(scheduled_at.isoformat())
            continue
        # 到期时刻距 now 不足一个 tick 周期的视为「准点触发」，否则为补跑
        is_backfill = (now - scheduled_at) > dt.timedelta(minutes=1)
        windows.append(
            ScheduledWindow(
                job_id=schedule.job_id,
                scheduled_at=scheduled_at,
                is_backfill=is_backfill,
            )
        )

    return windows, missed
