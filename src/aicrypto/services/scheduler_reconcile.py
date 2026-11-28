"""任务状态聚合与重试判定（纯函数）。

这些函数实现 P5-Part2 的核心逻辑，不涉及 ORM，可在调度层"对账回写"中独立使用。
"""

from __future__ import annotations

import datetime as dt


def aggregate_job_status(analysis_statuses: list[str]) -> str | None:
    """聚合一个 job_run 下所有 analysis_run 的状态（PRD 23.1.3）。

    **语义**（all_ok）：
    - 若有 RUNNING / QUEUED 未终态 → 返回 None（job_run 仍在进行）
    - 若全部 COMPLETE / PARTIAL → SUCCESS
    - 若任一 FAILED → FAILED（即使其他 PARTIAL）
    - 若任一 TIMEOUT → TIMEOUT（优先级最高）
    """
    if not analysis_statuses:
        # 窗口无任何标的（极端情况）：不应伪造成功
        return None

    terminal_statuses = {"COMPLETE", "PARTIAL", "FAILED", "TIMEOUT"}

    # 检查是否全部终态
    if not all(s in terminal_statuses for s in analysis_statuses):
        # 有 RUNNING 或 QUEUED，job 还在进行
        return None

    # 按优先级判定
    if "TIMEOUT" in analysis_statuses:
        return "TIMEOUT"

    if "FAILED" in analysis_statuses:
        return "FAILED"

    # 只有 COMPLETE / PARTIAL
    return "SUCCESS"


def is_timed_out(started_at: dt.datetime, timeout_seconds: int, *, now: dt.datetime) -> bool:
    """判断任务是否超时。"""
    deadline = started_at + dt.timedelta(seconds=timeout_seconds)
    return now > deadline


def should_retry(status: str, attempt: int, max_retries: int) -> bool:
    """判断是否应该重试。

    仅当：
    - status 是 FAILED
    - attempt < max_retries（还有配额）
    """
    return status == "FAILED" and attempt < max_retries


def next_retry_at(
    failed_at: dt.datetime, attempt: int, *, base: dt.timedelta | None = None
) -> dt.datetime:
    """计算下次重试时间（指数退避）。

    重试延迟 = base × 2^(attempt - 1)
    例：base=30s, attempt=1 → 30s, attempt=2 → 60s, attempt=3 → 120s
    """
    if base is None:
        base = dt.timedelta(seconds=30)
    delay = base * (2 ** (attempt - 1))
    return failed_at + delay
