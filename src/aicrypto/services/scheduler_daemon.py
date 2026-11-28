"""APScheduler 守护进程化——P6 调度层（PRD 23.1 / 23.3）。

**设计要点**：

1. **常驻运行**：替代外部 cron / systemd timer，改为进程内常驻调度。
2. **与现有架构兼容**：复用 `run_scheduler.py` 的业务逻辑（计算窗口、入队、对账）。
3. **故障隔离**：单次 tick 失败不影响下一次执行；异常完整记日志供排查。
4. **支持多实例**：通过分布式锁（L-30 任务）避免并发执行。
5. **热更新任务**：从 `scheduled_jobs` 表读取任务定义，支持不重启更新。

**使用方式**（P6 完成后替代 cron / systemd timer）：

    python scripts/run_scheduler_daemon.py

会一直运行，按时执行各任务。需要 supervisor / systemd 保证常驻。

**关键类**：

- :class:`APSchedulerDaemon`：主控类，建立并维护 APScheduler 实例。
- :class:`TaskJobStore`：从数据库加载任务定义的 job store。
- :class:`TaskScheduler`：包装现有的 `run_scheduler.main()` 逻辑。
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from aicrypto.common.clock import now
from aicrypto.config.settings import Settings, get_settings
from aicrypto.infrastructure.database.session import session_scope

if TYPE_CHECKING:
    from aicrypto.infrastructure.database.models.scheduler import ScheduledJob

logger = logging.getLogger(__name__)


class APSchedulerDaemon:
    """APScheduler 守护进程管理。

    职责：
    - 初始化 APScheduler 实例
    - 从数据库加载任务定义并注册到调度器
    - 处理异常，确保故障隔离
    - 支持优雅关闭
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        :param settings: 配置对象（若不提供则自动获取）
        """
        self.settings: Settings = settings or get_settings()
        self.scheduler: BackgroundScheduler = self._build_scheduler()

    def _build_scheduler(self) -> BackgroundScheduler:
        """初始化 APScheduler 调度器。

        **配置要点**：
        - job_defaults.coalesce=True：错过的 tick 只执行一次（不堆积）
        - job_defaults.max_instances=1：同一任务最多 1 个实例（需分布式锁）
        - misfire_grace_time=120s：错过 2 分钟内的 tick 仍视为应执行
        """
        return BackgroundScheduler(
            jobstores={"default": MemoryJobStore()},
            executors={"default": ThreadPoolExecutor(max_workers=4)},
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 120,
            },
            timezone="UTC",
        )

    def load_and_register_jobs(self) -> None:
        """从 scheduled_jobs 表加载任务定义并注册到调度器。

        **步骤**：
        1. 从数据库查询所有 enabled=true 的任务
        2. 对每个任务：
           - 若为 CRON 类型，用 CronTrigger 注册
           - 若为 INTERVAL 类型，用 IntervalTrigger 注册
           - 若无对应行则跳过（表可能为空，使用默认硬编码任务）
        3. 注册失败的任务记日志但不中断
        """
        try:
            from sqlalchemy import select

            from aicrypto.infrastructure.database.models.scheduler import ScheduledJob

            with session_scope(self.settings) as db:
                jobs = db.execute(
                    select(ScheduledJob).where(ScheduledJob.enabled == True)  # noqa: E712
                ).scalars().all()

            if not jobs:
                logger.warning(
                    "scheduled_jobs 表为空或无启用任务，使用默认任务清单（来自 run_scheduler.py）"
                )
                self._register_default_jobs()
            else:
                for job_def in jobs:
                    self._register_single_job(job_def)
                logger.info(f"已加载 {len(jobs)} 个调度任务")

        except Exception as exc:
            logger.error(f"加载任务定义失败: {exc}", exc_info=True)
            # 降级到默认任务清单（保证系统可用）
            logger.warning("降级到默认任务清单")
            self._register_default_jobs()

    def _register_single_job(self, job_def: ScheduledJob) -> None:
        """注册单个任务。

        :param job_def: ScheduledJob ORM 实例
        """
        try:
            if job_def.job_type == "CRON":
                trigger = CronTrigger.from_crontab(
                    job_def.cron_expr, timezone="UTC"
                )
                self.scheduler.add_job(
                    _execute_scheduler_tick,
                    trigger=trigger,
                    id=job_def.job_id,
                    name=f"[CRON] {job_def.job_id}",
                    args=(job_def.job_id,),
                    replace_existing=True,
                )
                logger.info(
                    f"已注册 CRON 任务: {job_def.job_id} cron={job_def.cron_expr}"
                )
            elif job_def.job_type == "INTERVAL":
                from apscheduler.triggers.interval import IntervalTrigger

                trigger = IntervalTrigger(seconds=job_def.interval_seconds)
                self.scheduler.add_job(
                    _execute_scheduler_tick,
                    trigger=trigger,
                    id=job_def.job_id,
                    name=f"[INTERVAL] {job_def.job_id}",
                    args=(job_def.job_id,),
                    replace_existing=True,
                )
                logger.info(
                    f"已注册 INTERVAL 任务: {job_def.job_id} interval={job_def.interval_seconds}s"
                )
            else:
                logger.warning(
                    f"未知任务类型: {job_def.job_type}（job_id={job_def.job_id}）"
                )

        except Exception as exc:
            logger.error(
                f"注册任务失败 (job_id={job_def.job_id}): {exc}", exc_info=True
            )

    def _register_default_jobs(self) -> None:
        """使用默认任务清单注册任务（来自 run_scheduler.py）。"""
        from aicrypto.services.scheduler_core import JobSchedule

        default_jobs = [
            JobSchedule(
                job_id="analysis_1h",
                job_type="CRON",
                cron_expr="5 * * * *",
                interval_seconds=None,
                timezone="UTC",
                max_backfill_window_seconds=86400,
                enabled=True,
            ),
            JobSchedule(
                job_id="analysis_15m",
                job_type="CRON",
                cron_expr="*/15 * * * *",
                interval_seconds=None,
                timezone="UTC",
                max_backfill_window_seconds=86400,
                enabled=True,
            ),
            JobSchedule(
                job_id="analysis_4h",
                job_type="CRON",
                cron_expr="7 */4 * * *",
                interval_seconds=None,
                timezone="UTC",
                max_backfill_window_seconds=86400,
                enabled=True,
            ),
            JobSchedule(
                job_id="analysis_daily",
                job_type="CRON",
                cron_expr="0 8 * * *",
                interval_seconds=None,
                timezone="UTC",
                max_backfill_window_seconds=86400,
                enabled=True,
            ),
        ]

        for job_def in default_jobs:
            trigger = CronTrigger.from_crontab(job_def.cron_expr, timezone="UTC")
            self.scheduler.add_job(
                _execute_scheduler_tick,
                trigger=trigger,
                id=job_def.job_id,
                name=f"[CRON] {job_def.job_id}",
                args=(job_def.job_id,),
                replace_existing=True,
            )
            logger.info(f"已注册默认 CRON 任务: {job_def.job_id}")

    def register_anomaly_scan(self) -> None:
        """注册异动扫描间隔任务（PRD 23.1.2，频率见 ANOMALY_SCAN_INTERVAL_SECONDS）。

        与分析调度解耦：异动不占用分析窗口，也不经过 run_scheduler。
        """
        from apscheduler.triggers.interval import IntervalTrigger

        cfg: Settings = self.settings
        interval = int(cfg.ANOMALY_SCAN_INTERVAL_SECONDS)
        self.scheduler.add_job(
            _execute_anomaly_scan,
            trigger=IntervalTrigger(seconds=interval),
            id="anomaly_scan",
            name="[INTERVAL] anomaly_scan",
            args=("anomaly_scan",),
            replace_existing=True,
        )
        logger.info(f"已注册异动扫描任务: interval={interval}s")

    def start(self) -> None:
        """启动调度器，进入前台运行（阻塞）。"""
        if not self.scheduler:
            logger.error("调度器未初始化")
            return

        try:
            logger.info("APScheduler 守护进程启动")
            self.scheduler.start()
            logger.info("调度器已启动，进入事件循环")
        except KeyboardInterrupt:
            logger.info("收到 SIGINT，优雅关闭")
            self.shutdown()
        except Exception as exc:
            logger.error(f"调度器异常: {exc}", exc_info=True)
            self.shutdown()
            raise

    def shutdown(self) -> None:
        """优雅关闭调度器。"""
        if self.scheduler and self.scheduler.running:
            logger.info("正在关闭调度器...")
            self.scheduler.shutdown(wait=True)
            logger.info("调度器已关闭")


def _run_tick() -> int:
    """加载 scripts/run_scheduler.py 并执行其 main()。

    scripts/ 不是包，且守护进程入口脚本的 sys.path 不含项目根目录，
    所以按文件路径加载，避免 ``from scripts.run_scheduler import`` 失败。
    """
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "scripts" / "run_scheduler.py"
    spec = importlib.util.spec_from_file_location("run_scheduler_tick", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载调度 tick 脚本: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


def _execute_anomaly_scan(job_id: str = "anomaly_scan") -> None:
    """异动扫描单次执行——由 APScheduler 按 ANOMALY_SCAN_INTERVAL_SECONDS 触发。

    - 独立于分析调度：异动不走 run_scheduler / 分析窗口；
    - 锁与去重由 ``run_anomaly_scan_once`` 自身负责（Redis ``lock:anomaly_scan``），
      此处不再叠加 DistributedLock，避免两把锁语义不一致；
    - 返回 SKIPPED_LOCKED 视为正常（上一轮尚未结束），不记错误；
    - 异常被记录但不抛出，保证下一次触发仍会执行。
    """
    from aicrypto.services.anomaly_runner import run_anomaly_scan_once

    try:
        result = run_anomaly_scan_once()
        if result.status == "SKIPPED_LOCKED":
            logger.info(f"异动扫描上一轮未结束，本轮跳过: job_id={job_id}")
        else:
            logger.debug(
                f"异动扫描完成: job_id={job_id} duration_ms={result.duration_ms:.0f}"
            )
    except Exception as exc:
        logger.error(f"异动扫描失败: job_id={job_id} error={exc}", exc_info=True)


def _execute_scheduler_tick(job_id: str) -> None:
    """单次调度 tick 的执行函数——由 APScheduler 触发。

    **设计要点**：
    - 每次触发时调用 `run_scheduler.main()` 的核心逻辑
    - 异常被 APScheduler 捕获并记日志，不影响下一次执行
    - 使用分布式锁确保多实例部署时只有一个实例执行（P6 任务二）
    """
    from aicrypto.services.scheduler_lock import DistributedLock

    # 使用分布式锁确保同一任务只在一个实例上执行
    with DistributedLock(job_id, timeout_seconds=120) as lock:
        if not lock.acquired:
            logger.info(
                f"未获得分布式锁，本轮跳过: job_id={job_id} "
                f"（可能其他实例正在执行）"
            )
            return

        # 获得锁，执行调度
        try:
            logger.debug(f"执行调度 tick: job_id={job_id} at {now()}")
            result = _run_tick()
            logger.debug(f"调度 tick 完成: job_id={job_id} result={result}")
        except Exception as exc:
            logger.error(
                f"调度 tick 执行失败: job_id={job_id} error={exc}", exc_info=True
            )
            # 不重新抛异常，让 APScheduler 捕获并记录，确保下一次执行正常


def main() -> int:
    """命令行入口。"""
    settings = get_settings()

    if not settings.SCHEDULER_ENABLED:
        logger.warning("SCHEDULER_ENABLED=false，守护进程不启动")
        return 0

    try:
        daemon = APSchedulerDaemon(settings)
        daemon.load_and_register_jobs()
        daemon.register_anomaly_scan()
        daemon.start()
        return 0
    except Exception as exc:
        logger.error(f"守护进程启动失败: {exc}", exc_info=True)
        return 1


if __name__ == "__main__":
    import logging.config

    # 简单日志配置（生产环境应使用 structlog）
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    sys.exit(main())
