"""job_run 终态聚合与超时/重试判定的单元测试（纯函数，无数据库）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aicrypto.services.scheduler_reconcile import (
    aggregate_job_status,
    is_timed_out,
    next_retry_at,
    should_retry,
)

T0 = datetime(2026, 11, 28, 12, 0, 0, tzinfo=UTC)


class TestAggregateJobStatus:
    """all_ok 语义：全部 COMPLETE/PARTIAL → SUCCESS；任一 FAILED → FAILED；任一 TIMEOUT → TIMEOUT。"""

    def test_all_complete_is_success(self) -> None:
        assert aggregate_job_status(["COMPLETE", "COMPLETE"]) == "SUCCESS"

    def test_partial_counts_as_success(self) -> None:
        assert aggregate_job_status(["COMPLETE", "PARTIAL"]) == "SUCCESS"

    def test_any_failed_is_failed(self) -> None:
        assert aggregate_job_status(["COMPLETE", "FAILED"]) == "FAILED"

    def test_timeout_takes_precedence_over_failed(self) -> None:
        assert aggregate_job_status(["FAILED", "TIMEOUT"]) == "TIMEOUT"

    def test_unfinished_returns_none(self) -> None:
        # 仍有 QUEUED / RUNNING 的标的时，job_run 尚未终态
        assert aggregate_job_status(["COMPLETE", "RUNNING"]) is None
        assert aggregate_job_status(["QUEUED"]) is None

    def test_no_children_returns_none(self) -> None:
        # 窗口存在但没有任何标的运行（如市场未配置）：不能伪造 SUCCESS
        assert aggregate_job_status([]) is None


class TestTimeout:
    def test_within_budget_not_timed_out(self) -> None:
        assert not is_timed_out(T0, timeout_seconds=600, now=T0 + timedelta(seconds=599))

    def test_beyond_budget_timed_out(self) -> None:
        assert is_timed_out(T0, timeout_seconds=600, now=T0 + timedelta(seconds=601))


class TestRetry:
    def test_failed_below_max_retries_should_retry(self) -> None:
        assert should_retry(status="FAILED", attempt=1, max_retries=3)

    def test_attempt_reaches_max_no_retry(self) -> None:
        assert not should_retry(status="FAILED", attempt=3, max_retries=3)

    def test_success_never_retries(self) -> None:
        assert not should_retry(status="SUCCESS", attempt=1, max_retries=3)

    def test_backoff_is_exponential(self) -> None:
        base = timedelta(seconds=30)
        assert next_retry_at(T0, attempt=1, base=base) == T0 + timedelta(seconds=30)
        assert next_retry_at(T0, attempt=2, base=base) == T0 + timedelta(seconds=60)
        assert next_retry_at(T0, attempt=3, base=base) == T0 + timedelta(seconds=120)
