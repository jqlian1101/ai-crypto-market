"""APScheduler 守护进程测试。"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest
from apscheduler.schedulers.base import SchedulerAlreadyRunningError

from aicrypto.services.scheduler_daemon import APSchedulerDaemon


class TestAPSchedulerDaemon:
    """APScheduler 守护进程的初始化与任务注册。"""

    def test_daemon_initializes_scheduler(self):
        """守护进程初始化时应创建 BackgroundScheduler 实例。"""
        with patch("aicrypto.services.scheduler_daemon.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_get_settings.return_value = mock_settings
            
            daemon = APSchedulerDaemon(settings=mock_settings)
            assert daemon.scheduler is not None
            assert daemon.scheduler.state == 0  # SchedulerState.STOPPED

    def test_daemon_loads_default_jobs_when_table_empty(self):
        """scheduled_jobs 表为空时降级到默认任务清单。"""
        with patch("aicrypto.services.scheduler_daemon.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_get_settings.return_value = mock_settings
            
            with patch("aicrypto.services.scheduler_daemon.session_scope") as mock_scope:
                mock_db = MagicMock()
                mock_scope.return_value.__enter__.return_value = mock_db
                mock_db.execute.return_value.scalars.return_value.all.return_value = []

                daemon = APSchedulerDaemon(settings=mock_settings)
                daemon.load_and_register_jobs()

                # 应该注册 4 个默认任务
                jobs = daemon.scheduler.get_jobs()
                assert len(jobs) == 4
                job_ids = {job.id for job in jobs}
                assert job_ids == {
                    "analysis_1h",
                    "analysis_15m",
                    "analysis_4h",
                    "analysis_daily",
                }

    def test_daemon_registers_cron_job_from_db(self):
        """从数据库加载 CRON 任务并正确注册。"""
        from sqlalchemy.orm import Session

        from aicrypto.infrastructure.database.models.scheduler import ScheduledJob

        with patch("aicrypto.services.scheduler_daemon.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_get_settings.return_value = mock_settings

            with patch("aicrypto.services.scheduler_daemon.session_scope") as mock_scope:
                mock_db = MagicMock(spec=Session)
                mock_scope.return_value.__enter__.return_value = mock_db

                # 模拟数据库返回一个 CRON 任务
                job_def = MagicMock(spec=ScheduledJob)
                job_def.job_id = "test_cron"
                job_def.job_type = "CRON"
                job_def.cron_expr = "0 12 * * *"  # 每日午夜 UTC
                job_def.interval_seconds = None

                mock_db.execute.return_value.scalars.return_value.all.return_value = [
                    job_def
                ]

                daemon = APSchedulerDaemon(settings=mock_settings)
                daemon.load_and_register_jobs()

                # 应该注册了该任务
                job = daemon.scheduler.get_job("test_cron")
                assert job is not None
                assert job.name == "[CRON] test_cron"

    def test_daemon_registers_interval_job_from_db(self):
        """从数据库加载 INTERVAL 任务并正确注册。"""
        with patch("aicrypto.services.scheduler_daemon.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_get_settings.return_value = mock_settings

            with patch("aicrypto.services.scheduler_daemon.session_scope") as mock_scope:
                mock_db = MagicMock()
                mock_scope.return_value.__enter__.return_value = mock_db

                # 模拟 INTERVAL 任务
                job_def = MagicMock()
                job_def.job_id = "test_interval"
                job_def.job_type = "INTERVAL"
                job_def.cron_expr = None
                job_def.interval_seconds = 60

                mock_db.execute.return_value.scalars.return_value.all.return_value = [
                    job_def
                ]

                daemon = APSchedulerDaemon(settings=mock_settings)
                daemon.load_and_register_jobs()

                job = daemon.scheduler.get_job("test_interval")
                assert job is not None
                assert job.name == "[INTERVAL] test_interval"

    def test_daemon_skips_invalid_job_type(self):
        """遇到未知任务类型时应跳过但不中断。"""
        with patch("aicrypto.services.scheduler_daemon.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_get_settings.return_value = mock_settings

            with patch("aicrypto.services.scheduler_daemon.session_scope") as mock_scope:
                mock_db = MagicMock()
                mock_scope.return_value.__enter__.return_value = mock_db

                # 未知类型
                job_def = MagicMock()
                job_def.job_id = "unknown_job"
                job_def.job_type = "UNKNOWN"

                mock_db.execute.return_value.scalars.return_value.all.return_value = [
                    job_def
                ]

                daemon = APSchedulerDaemon(settings=mock_settings)
                daemon.load_and_register_jobs()

                # 不应注册该任务
                job = daemon.scheduler.get_job("unknown_job")
                assert job is None

    def test_daemon_replaces_existing_jobs(self):
        """同一 job_id 的新注册应替换旧的。"""
        with patch("aicrypto.services.scheduler_daemon.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_get_settings.return_value = mock_settings

            with patch("aicrypto.services.scheduler_daemon.session_scope") as mock_scope:
                mock_db = MagicMock()
                mock_scope.return_value.__enter__.return_value = mock_db

                job_def = MagicMock()
                job_def.job_id = "replacement_job"
                job_def.job_type = "CRON"
                job_def.cron_expr = "0 12 * * *"

                mock_db.execute.return_value.scalars.return_value.all.return_value = [
                    job_def
                ]

                daemon = APSchedulerDaemon(settings=mock_settings)
                daemon.load_and_register_jobs()

                job1_id = daemon.scheduler.get_job("replacement_job").id

                # 再次加载（模拟配置更新）
                daemon.load_and_register_jobs()

                job2_id = daemon.scheduler.get_job("replacement_job").id

                # job_id 应保持一致（即使是替换）
                assert job1_id == job2_id

    def test_daemon_handles_cron_parse_error_gracefully(self):
        """无效的 cron 表达式应被捕获并跳过。"""
        with patch("aicrypto.services.scheduler_daemon.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_get_settings.return_value = mock_settings

            with patch("aicrypto.services.scheduler_daemon.session_scope") as mock_scope:
                mock_db = MagicMock()
                mock_scope.return_value.__enter__.return_value = mock_db

                # 无效的 cron
                job_def = MagicMock()
                job_def.job_id = "bad_cron"
                job_def.job_type = "CRON"
                job_def.cron_expr = "* * * * * * *"  # 6 字段（无效）

                mock_db.execute.return_value.scalars.return_value.all.return_value = [
                    job_def
                ]

                daemon = APSchedulerDaemon(settings=mock_settings)
                # 应不抛异常，只记日志
                daemon.load_and_register_jobs()

                job = daemon.scheduler.get_job("bad_cron")
                assert job is None


class TestExecuteSchedulerTick:
    """_execute_scheduler_tick 的异常隔离测试。"""

    def test_execute_scheduler_tick_catches_exceptions(self):
        """单次 tick 执行的异常应被捕获，不向外传播。"""
        from aicrypto.services.scheduler_daemon import _execute_scheduler_tick

        with patch("aicrypto.services.scheduler_daemon._run_tick") as mock_tick:
            with patch("aicrypto.services.scheduler_lock.DistributedLock") as mock_lock_class:
                mock_lock = MagicMock()
                mock_lock.acquired = True
                mock_lock_class.return_value.__enter__.return_value = mock_lock
                
                # 模拟异常
                mock_tick.side_effect = RuntimeError("Database connection failed")

                # 应不抛异常
                _execute_scheduler_tick("test_job")

    def test_execute_scheduler_tick_logs_success(self):
        """成功执行应记日志。"""
        from aicrypto.services.scheduler_daemon import _execute_scheduler_tick

        with patch(
            "aicrypto.services.scheduler_daemon._run_tick"
        ) as mock_tick:
            with patch("aicrypto.services.scheduler_lock.DistributedLock") as mock_lock_class:
                mock_lock = MagicMock()
                mock_lock.acquired = True
                mock_lock_class.return_value.__enter__.return_value = mock_lock
                
                mock_tick.return_value = 0

                _execute_scheduler_tick("test_job")


class TestDaemonShutdown:
    """守护进程关闭逻辑。"""

    def test_daemon_shutdown_gracefully(self):
        """优雅关闭应停止调度器。"""
        with patch("aicrypto.services.scheduler_daemon.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_get_settings.return_value = mock_settings

            daemon = APSchedulerDaemon(settings=mock_settings)
            daemon.load_and_register_jobs()

            # 模拟启动（但不真的运行事件循环）
            daemon.scheduler.start()
            assert daemon.scheduler.running

            daemon.shutdown()
            assert not daemon.scheduler.running
