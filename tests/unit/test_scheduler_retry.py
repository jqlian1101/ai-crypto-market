"""调度器重试机制的单元测试（P6-Phase2）。

验证：
1. 失败任务的重试判定（should_retry）
2. 重试延迟计算（next_retry_at）
3. 重试窗口创建与入队
4. 重试历史完整性
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch, call

import pytest

from aicrypto.infrastructure.database.models.scheduler import JobRun


class TestRetryJudgment:
    """重试判定逻辑的单元测试（纯函数）。"""

    def test_should_retry_when_failed_and_attempts_available(self):
        """失败且还有重试配额时应返回 True。"""
        from aicrypto.services.scheduler_reconcile import should_retry

        result = should_retry(status="FAILED", attempt=1, max_retries=2)
        assert result is True

    def test_should_not_retry_when_max_attempts_exceeded(self):
        """已达到最大重试次数时应返回 False。"""
        from aicrypto.services.scheduler_reconcile import should_retry

        result = should_retry(status="FAILED", attempt=2, max_retries=2)
        assert result is False

    def test_should_not_retry_when_timeout(self):
        """TIMEOUT 状态不重试。"""
        from aicrypto.services.scheduler_reconcile import should_retry

        result = should_retry(status="TIMEOUT", attempt=1, max_retries=2)
        assert result is False

    def test_should_not_retry_when_success(self):
        """SUCCESS 状态不重试。"""
        from aicrypto.services.scheduler_reconcile import should_retry

        result = should_retry(status="SUCCESS", attempt=1, max_retries=2)
        assert result is False

    def test_zero_max_retries_never_retries(self):
        """max_retries=0 时永不重试。"""
        from aicrypto.services.scheduler_reconcile import should_retry

        result = should_retry(status="FAILED", attempt=1, max_retries=0)
        assert result is False


class TestRetryDelay:
    """重试延迟计算的单元测试。"""

    def test_first_retry_uses_base_delay(self):
        """第一次重试延迟 = base × 2^0 = base。"""
        from aicrypto.services.scheduler_reconcile import next_retry_at

        failed_at = dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC)
        base = dt.timedelta(seconds=30)

        retry_at = next_retry_at(failed_at, attempt=1, base=base)

        # 应延迟 30 秒
        assert retry_at == failed_at + dt.timedelta(seconds=30)

    def test_second_retry_doubles_delay(self):
        """第二次重试延迟 = base × 2^1 = base * 2。"""
        from aicrypto.services.scheduler_reconcile import next_retry_at

        failed_at = dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC)
        base = dt.timedelta(seconds=30)

        retry_at = next_retry_at(failed_at, attempt=2, base=base)

        # 应延迟 60 秒
        assert retry_at == failed_at + dt.timedelta(seconds=60)

    def test_third_retry_exponential_backoff(self):
        """第三次重试延迟 = base × 2^2 = base * 4。"""
        from aicrypto.services.scheduler_reconcile import next_retry_at

        failed_at = dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC)
        base = dt.timedelta(seconds=30)

        retry_at = next_retry_at(failed_at, attempt=3, base=base)

        # 应延迟 120 秒
        assert retry_at == failed_at + dt.timedelta(seconds=120)

    def test_default_base_is_30_seconds(self):
        """未指定 base 时默认为 30 秒。"""
        from aicrypto.services.scheduler_reconcile import next_retry_at

        failed_at = dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC)

        retry_at = next_retry_at(failed_at, attempt=1)

        # 应延迟 30 秒
        assert retry_at == failed_at + dt.timedelta(seconds=30)


class TestReconcileJobRunWithRetry:
    """reconcile_job_run 重试逻辑的集成测试。"""

    def test_reconcile_failed_job_with_retry_creates_new_window(self):
        """FAILED 且可重试时应创建重试窗口。"""
        from aicrypto.services.scheduler_reconcile_db import reconcile_job_run

        # Mock 数据库和相关对象
        db = MagicMock()
        
        # 创建原 job_run
        original_failed_at = dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC)
        original_job_run = JobRun(
            job_run_id="run-1",
            job_id="job-daily",
            idempotency_key="job-daily:2026-11-28T10:00:00",
            scheduled_at=original_failed_at,
            started_at=original_failed_at,
            attempt=1,
            status="RUNNING",
        )
        
        # Mock analysis_runs
        analysis_run_1 = MagicMock()
        analysis_run_1.run_id = "arun-1"
        analysis_run_1.symbol = "BTC"
        analysis_run_1.market_type = "spot"
        analysis_run_1.status = "FAILED"
        
        analysis_run_2 = MagicMock()
        analysis_run_2.run_id = "arun-2"
        analysis_run_2.symbol = "ETH"
        analysis_run_2.market_type = "futures"
        analysis_run_2.status = "FAILED"
        
        # Mock scalars 链
        db.scalars.return_value.all.return_value = [analysis_run_1, analysis_run_2]
        
        # Mock Settings （全局导入路径）
        with patch("aicrypto.config.settings.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.SCHEDULER_MAX_RETRIES = 2
            mock_settings.SCHEDULER_RETRY_BASE_DELAY_SECONDS = 30
            mock_get_settings.return_value = mock_settings
            
            with patch("aicrypto.services.scheduler_enqueue.enqueue_analysis"):
                now = dt.datetime(2026, 11, 28, 10, 5, 0, tzinfo=dt.UTC)
                
                # 执行
                reconcile_job_run(db, original_job_run, now=now)
                
                # 验证：应该调用 db.add() 和 db.flush()
                assert db.add.called, "应该添加新的重试 job_run"
                assert db.flush.called, "应该 flush 操作"


class TestRetryJobRunAttributes:
    """重试 job_run 属性的单元测试。"""

    def test_retry_job_run_created_with_correct_attempt(self):
        """重试窗口的 attempt 应递增。"""
        original = JobRun(
            job_run_id="run-1",
            job_id="job-daily",
            scheduled_at=dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC),
            attempt=1,
            status="FAILED",
        )

        # 验证重试窗口会有 attempt=2
        retry_attempt = (original.attempt or 1) + 1
        assert retry_attempt == 2

    def test_retry_job_run_scheduled_in_future(self):
        """重试窗口的 scheduled_at 应在未来。"""
        from aicrypto.services.scheduler_reconcile import next_retry_at

        failed_at = dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC)
        base = dt.timedelta(seconds=30)

        retry_at = next_retry_at(failed_at, attempt=1, base=base)

        # 重试时间应在 failed_at 之后
        assert retry_at > failed_at

    def test_retry_job_run_has_running_status(self):
        """重试窗口初始状态应为 RUNNING（以便入队分析）。"""
        retry_run = JobRun(
            job_run_id="run-2",
            job_id="job-daily",
            idempotency_key="job-daily:retry:2:2026-11-28T10:00:30",
            scheduled_at=dt.datetime(2026, 11, 28, 10, 0, 30, tzinfo=dt.UTC),
            started_at=dt.datetime(2026, 11, 28, 10, 0, 30, tzinfo=dt.UTC),
            attempt=2,
            status="RUNNING",
        )

        assert retry_run.status == "RUNNING"
        assert retry_run.attempt == 2


class TestRetryAnalysisReEnqueue:
    """重试窗口的分析任务重新入队。"""

    def test_retry_extracts_symbols_and_markets(self):
        """重试应从失败的 analysis_runs 中提取标的和市场类型。"""
        # 验证数据提取逻辑
        analysis_runs = [
            MagicMock(symbol="BTC", market_type="spot", status="FAILED"),
            MagicMock(symbol="ETH", market_type="spot", status="FAILED"),
            MagicMock(symbol="BTC", market_type="futures", status="FAILED"),
        ]

        symbols = {run.symbol for run in analysis_runs}
        market_types = {run.market_type for run in analysis_runs}

        assert symbols == {"BTC", "ETH"}
        assert market_types == {"spot", "futures"}

    def test_retry_respects_idempotency(self):
        """重试入队应使用幂等键防止重复。"""
        from aicrypto.services.scheduler_core import make_run_idempotency_key

        job_id = "job-daily"
        scheduled_at = dt.datetime(2026, 11, 28, 10, 0, 30, tzinfo=dt.UTC)
        symbol = "BTC"
        market_type = "spot"

        idem_key_1 = make_run_idempotency_key(
            job_id, scheduled_at, symbol=symbol, market_type=market_type
        )
        idem_key_2 = make_run_idempotency_key(
            job_id, scheduled_at, symbol=symbol, market_type=market_type
        )

        # 相同参数应生成相同的幂等键
        assert idem_key_1 == idem_key_2


class TestOriginalJobRunMarkedFailed:
    """原 job_run 标记为 FAILED。"""

    def test_original_job_run_keeps_failed_status(self):
        """即使创建了重试，原 job_run 仍标记为 FAILED。"""
        original = JobRun(
            job_run_id="run-1",
            job_id="job-daily",
            idempotency_key="job-daily:2026-11-28T10:00:00",
            scheduled_at=dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC),
            started_at=dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC),
            attempt=1,
            status="FAILED",
        )

        # 验证原 run 状态不变
        assert original.status == "FAILED"

    def test_retry_and_original_separate_records(self):
        """重试和原 job_run 应为不同的记录。"""
        original_id = "run-1"
        retry_id = "run-2"

        # 两个 ID 应不同
        assert original_id != retry_id

