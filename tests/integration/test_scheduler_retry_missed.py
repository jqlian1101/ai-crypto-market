"""调度器重试与 MISSED 窗口的 PostgreSQL 集成测试（P6-Phase2）。

使用真实 Postgres 验证：
1. reconcile_job_run 的重试分支能真正插入 job_runs 与 analysis_runs；
2. 重试窗口的幂等键、CHECK 约束（status 枚举）、NOT NULL 约束均满足；
3. record_missed_windows 真实落库，且重复调用幂等；
4. MISSED 记录不会触发任何分析入队。

所有写入在测试结束后回滚（外层事务 + rollback），不污染库。
"""

from __future__ import annotations

import datetime as dt
import os

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from aicrypto.infrastructure.database.models.report import AnalysisRunRecord
from aicrypto.infrastructure.database.models.scheduler import JobRun, ScheduledJob
from aicrypto.services.scheduler_reconcile_db import (
    reconcile_job_run,
    record_missed_windows,
)

pytestmark = pytest.mark.integration

JOB_ID = "it_test_retry_job"
T0 = dt.datetime(2026, 11, 28, 10, 0, 0, tzinfo=dt.UTC)


RETRY_KW = {
    "max_retries": 2,
    "retry_base_delay": dt.timedelta(seconds=30),
    "retry_targets": [("BTCUSDT", "SPOT"), ("ETHUSDT", "SPOT"), ("SOLUSDT", "SPOT")],
}


@pytest.fixture
def db_session():
    url = os.getenv("ANALYSIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 ANALYSIS_TEST_DATABASE_URL 以执行 PostgreSQL 集成测试")

    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(connection, expire_on_commit=False)
        try:
            # 测试用的任务定义（外键依赖）
            session.add(
                ScheduledJob(
                    job_id=JOB_ID,
                    job_type="CRON",
                    cron_expr="0 * * * *",
                    timezone="UTC",
                    timeout_seconds=600,
                    max_retries=2,
                    updated_at=T0,
                )
            )
            session.flush()
            yield session
        finally:
            session.close()
            transaction.rollback()
    engine.dispose()


def _failed_parent(session: Session, *, attempt: int = 1) -> JobRun:
    """构造一个 RUNNING 的父窗口，并挂两条 FAILED 的下属分析。"""
    parent = JobRun(
        job_run_id=f"it-parent-{attempt}",
        job_id=JOB_ID,
        idempotency_key=f"{JOB_ID}:{T0.isoformat()}",
        scheduled_at=T0,
        started_at=T0,
        status="RUNNING",
        attempt=attempt,
        is_backfill=False,
    )
    session.add(parent)
    session.flush()

    for symbol in ("BTCUSDT", "ETHUSDT"):
        session.add(
            AnalysisRunRecord(
                run_id=f"it-run-{symbol}-{attempt}",
                analysis_id=f"it-analysis-{symbol}-{attempt}",
                symbol=symbol,
                market_type="SPOT",
                trigger="SCHEDULED",
                idempotency_key=f"it-parent-key-{symbol}-{attempt}",
                job_run_id=parent.job_run_id,
                status="FAILED",
                analysis_mode="LIVE",
                created_at=T0,
            )
        )
    session.flush()
    return parent


class TestRetryPersistence:
    def test_failed_window_inserts_retry_job_run(self, db_session: Session) -> None:
        """FAILED 且可重试：应真实插入一条 attempt=2 的重试 job_run。"""
        parent = _failed_parent(db_session, attempt=1)
        now = T0 + dt.timedelta(minutes=5)

        reconcile_job_run(db_session, parent, now=now, **RETRY_KW)
        db_session.flush()

        assert parent.status == "FAILED"
        retries = db_session.scalars(
            select(JobRun).where(JobRun.job_id == JOB_ID, JobRun.attempt == 2)
        ).all()
        assert len(retries) == 1
        assert retries[0].status == "RUNNING"
        assert retries[0].idempotency_key  # NOT NULL + 唯一键已满足

    def test_retry_enqueues_analysis_for_each_failed_run(self, db_session: Session) -> None:
        """重试窗口应为每个失败的 (symbol, market_type) 入队一条分析。"""
        parent = _failed_parent(db_session, attempt=1)
        reconcile_job_run(db_session, parent, now=T0 + dt.timedelta(minutes=5), **RETRY_KW)
        db_session.flush()

        retry = db_session.scalars(
            select(JobRun).where(JobRun.job_id == JOB_ID, JobRun.attempt == 2)
        ).one()
        queued = db_session.scalars(
            select(AnalysisRunRecord).where(AnalysisRunRecord.job_run_id == retry.job_run_id)
        ).all()
        pairs = sorted((r.symbol, r.market_type) for r in queued)
        # 完整清单 3 个组合，而非原窗口残留的 2 个
        assert pairs == [("BTCUSDT", "SPOT"), ("ETHUSDT", "SPOT"), ("SOLUSDT", "SPOT")]

    def test_retry_fills_combinations_missing_from_original(self, db_session: Session) -> None:
        """原窗口只有 BTC 一条记录（SOL 从未入队），重试应按完整清单补上 SOL。"""
        parent = JobRun(
            job_run_id="it-parent-partial",
            job_id=JOB_ID,
            idempotency_key=f"{JOB_ID}:partial:{T0.isoformat()}",
            scheduled_at=T0,
            started_at=T0,
            status="RUNNING",
            attempt=1,
            is_backfill=False,
        )
        db_session.add(parent)
        db_session.flush()
        db_session.add(
            AnalysisRunRecord(
                run_id="it-run-partial-btc",
                analysis_id="it-analysis-partial-btc",
                symbol="BTCUSDT",
                market_type="SPOT",
                trigger="SCHEDULED",
                idempotency_key="it-partial-key-btc",
                job_run_id=parent.job_run_id,
                status="FAILED",
                analysis_mode="LIVE",
                created_at=T0,
            )
        )
        db_session.flush()

        reconcile_job_run(db_session, parent, now=T0 + dt.timedelta(minutes=5), **RETRY_KW)
        db_session.flush()

        retry = db_session.scalars(
            select(JobRun).where(JobRun.job_id == JOB_ID, JobRun.attempt == 2)
        ).one()
        queued = db_session.scalars(
            select(AnalysisRunRecord).where(AnalysisRunRecord.job_run_id == retry.job_run_id)
        ).all()
        symbols = sorted(r.symbol for r in queued)
        assert "SOLUSDT" in symbols, "重试应按完整清单补上原窗口缺失的 SOL"

    def test_no_retry_when_attempts_exhausted(self, db_session: Session) -> None:
        """attempt 已达上限（max_retries=2）：不应插入重试 job_run。"""
        parent = _failed_parent(db_session, attempt=2)
        reconcile_job_run(db_session, parent, now=T0 + dt.timedelta(minutes=5), **RETRY_KW)
        db_session.flush()

        count = db_session.scalar(
            select(func.count())
            .select_from(JobRun)
            .where(JobRun.job_id == JOB_ID, JobRun.attempt == 3)
        )
        assert count == 0


class TestMissedPersistence:
    def test_record_missed_windows_inserts_rows(self, db_session: Session) -> None:
        windows = [T0, T0 + dt.timedelta(hours=1)]
        inserted = record_missed_windows(db_session, JOB_ID, windows, now=T0)
        db_session.flush()

        assert inserted == 2
        rows = db_session.scalars(
            select(JobRun).where(JobRun.job_id == JOB_ID, JobRun.status == "MISSED")
        ).all()
        assert len(rows) == 2

    def test_record_missed_windows_is_idempotent(self, db_session: Session) -> None:
        windows = [T0]
        first = record_missed_windows(db_session, JOB_ID, windows, now=T0)
        db_session.flush()
        second = record_missed_windows(db_session, JOB_ID, windows, now=T0)
        db_session.flush()

        assert first == 1
        assert second == 0

    def test_missed_does_not_enqueue_analysis(self, db_session: Session) -> None:
        record_missed_windows(db_session, JOB_ID, [T0], now=T0)
        db_session.flush()

        count = db_session.scalar(
            select(func.count())
            .select_from(AnalysisRunRecord)
            .join(JobRun, AnalysisRunRecord.job_run_id == JobRun.job_run_id)
            .where(JobRun.job_id == JOB_ID, JobRun.status == "MISSED")
        )
        assert count == 0
