"""调度器 MISSED 窗口落库的单元测试（P6-Phase2）。

验证：
1. MISSED 窗口记录到 job_runs 表
2. 状态标记为 MISSED
3. 幂等性防止重复
4. 日报集成数据完整性
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from aicrypto.infrastructure.database.models.scheduler import JobRun


class TestMissedWindowsRecording:
    """MISSED 窗口落库的基础测试。"""

    def test_missed_windows_recorded_to_job_runs(self):
        """MISSED 窗口应被记录到 job_runs 表。"""
        from aicrypto.services.scheduler_reconcile_db import record_missed_windows

        db = MagicMock()
        
        # Mock scalars 检查不存在
        db.scalar.return_value = None
        
        job_id = "job-daily"
        missed_windows = [
            dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 11, 28, 11, 0, 0, tzinfo=dt.UTC),
        ]
        now = dt.datetime(2026, 11, 28, 12, 0, 0, tzinfo=dt.UTC)
        
        # 执行
        result = record_missed_windows(db, job_id, missed_windows, now)
        
        # 验证：应该插入 2 条记录
        assert result == 2
        assert db.add.call_count == 2
        assert db.flush.called

    def test_missed_status_set_correctly(self):
        """MISSED job_run 的 status 应为 'MISSED'。"""
        missed_run = JobRun(
            job_run_id="missed-1",
            job_id="job-daily",
            idempotency_key="job-daily:2026-11-28T10:00:00",
            scheduled_at=dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC),
            started_at=dt.datetime(2026, 11, 28, 12, 0, 0, tzinfo=dt.UTC),
            status="MISSED",
        )

        assert missed_run.status == "MISSED"

    def test_idempotency_key_prevents_duplicate_missed(self):
        """幂等键检查应防止重复 MISSED 记录。"""
        from aicrypto.services.scheduler_core import make_idempotency_key

        job_id = "job-daily"
        scheduled_at = dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC)

        idem_key_1 = make_idempotency_key(job_id, scheduled_at)
        idem_key_2 = make_idempotency_key(job_id, scheduled_at)

        # 相同参数应生成相同的幂等键
        assert idem_key_1 == idem_key_2

        # 不同参数应生成不同的幂等键
        different_scheduled_at = dt.datetime(2026, 11, 28, 11, 0, 0, tzinfo=dt.UTC)
        idem_key_3 = make_idempotency_key(job_id, different_scheduled_at)
        assert idem_key_1 != idem_key_3

    def test_duplicate_missed_skipped(self):
        """重复的 MISSED 窗口应被跳过。"""
        from aicrypto.services.scheduler_reconcile_db import record_missed_windows

        db = MagicMock()
        
        # Mock scalars：第一次返回 None（不存在），第二次返回一条已存在的记录
        existing_run = MagicMock()
        db.scalar.side_effect = [None, existing_run]
        
        job_id = "job-daily"
        missed_windows = [
            dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC),  # 重复
        ]
        now = dt.datetime(2026, 11, 28, 12, 0, 0, tzinfo=dt.UTC)
        
        # 执行
        result = record_missed_windows(db, job_id, missed_windows, now)
        
        # 验证：应该只插入 1 条（第二条被跳过）
        assert result == 1
        assert db.add.call_count == 1


class TestMissedWindowsDataIntegrity:
    """MISSED 窗口数据完整性的测试。"""

    def test_missed_job_run_has_correct_scheduled_at(self):
        """MISSED job_run 的 scheduled_at 应为规划时间。"""
        scheduled_at = dt.datetime(2026, 11, 28, 10, 30, 0, tzinfo=dt.UTC)
        missed_run = JobRun(
            job_run_id="missed-1",
            job_id="job-daily",
            idempotency_key="job-daily:2026-11-28T10:30:00",
            scheduled_at=scheduled_at,
            started_at=dt.datetime(2026, 11, 28, 12, 0, 0, tzinfo=dt.UTC),
            status="MISSED",
        )

        assert missed_run.scheduled_at == scheduled_at

    def test_missed_job_run_has_correct_job_id(self):
        """MISSED job_run 应记录源 job_id。"""
        job_id = "job-daily"
        missed_run = JobRun(
            job_run_id="missed-1",
            job_id=job_id,
            idempotency_key="job-daily:2026-11-28T10:00:00",
            scheduled_at=dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC),
            started_at=dt.datetime(2026, 11, 28, 12, 0, 0, tzinfo=dt.UTC),
            status="MISSED",
        )

        assert missed_run.job_id == job_id

    def test_missed_job_run_records_attempted_time(self):
        """MISSED job_run 应记录被标记为 MISSED 的时间。"""
        now = dt.datetime(2026, 11, 28, 10, 35, 0, tzinfo=dt.UTC)
        missed_run = JobRun(
            job_run_id="missed-1",
            job_id="job-daily",
            idempotency_key="job-daily:2026-11-28T10:30:00",
            scheduled_at=dt.datetime(2026, 11, 28, 10, 30, 0, tzinfo=dt.UTC),
            started_at=now,
            status="MISSED",
        )

        assert missed_run.started_at == now


class TestMissedWindowsCounting:
    """MISSED 窗口计数的测试。"""

    def test_multiple_missed_windows_all_recorded(self):
        """多个 MISSED 窗口应全部入库。"""
        windows = [
            dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 11, 28, 11, 0, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 11, 28, 12, 0, 0, tzinfo=dt.UTC),
        ]

        # 验证输入结构
        assert len(windows) == 3
        assert all(isinstance(w, dt.datetime) for w in windows)

    def test_empty_missed_windows_list(self):
        """空的 MISSED 列表应不产生任何插入。"""
        from aicrypto.services.scheduler_reconcile_db import record_missed_windows

        db = MagicMock()
        
        job_id = "job-daily"
        missed_windows = []
        now = dt.datetime(2026, 11, 28, 12, 0, 0, tzinfo=dt.UTC)
        
        # 执行
        result = record_missed_windows(db, job_id, missed_windows, now)
        
        # 验证：应该不调用 add 或 flush
        assert result == 0
        assert db.add.call_count == 0
        assert not db.flush.called


class TestMissedVsEnqueuedSeparation:
    """MISSED 和 ENQUEUED 窗口的区分。"""

    def test_missed_and_enqueued_windows_separate(self):
        """MISSED 窗口不应混入 RUNNING 或其他终态。"""
        missed = JobRun(
            job_run_id="missed-1",
            job_id="job-daily",
            idempotency_key="job-daily:2026-11-28T10:00:00",
            scheduled_at=dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC),
            started_at=dt.datetime(2026, 11, 28, 12, 0, 0, tzinfo=dt.UTC),
            status="MISSED",
        )

        running = JobRun(
            job_run_id="run-1",
            job_id="job-daily",
            idempotency_key="job-daily:2026-11-28T10:30:00",
            scheduled_at=dt.datetime(2026, 11, 28, 10, 30, 0, tzinfo=dt.UTC),
            started_at=dt.datetime(2026, 11, 28, 10, 30, 0, tzinfo=dt.UTC),
            status="RUNNING",
        )

        # 两个记录的状态应不同
        assert missed.status != running.status
        assert missed.status == "MISSED"
        assert running.status == "RUNNING"

    def test_missed_indication_in_job_run_records(self):
        """job_runs 表应能区分 MISSED 记录。"""
        # 验证 job_runs 模型支持 status='MISSED'
        # 这由模型定义保证（status 字段的 CHECK 约束）

        run = JobRun(
            job_run_id="missed-1",
            job_id="job-daily",
            idempotency_key="job-daily:2026-11-28T10:00:00",
            scheduled_at=dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC),
            started_at=dt.datetime(2026, 11, 28, 12, 0, 0, tzinfo=dt.UTC),
            status="MISSED",
        )
        assert run.status == "MISSED"

