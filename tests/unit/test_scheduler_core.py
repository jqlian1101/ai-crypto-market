"""调度核心函数的单元测试。

由于 shell 目前不可用，这些测试暂无法在 CI 中执行。
本地验证需运行：

    .venv\\Scripts\\pytest.exe -xvs tests/unit/test_scheduler_core.py
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aicrypto.services.scheduler_core import (
    JobSchedule,
    compute_backfill_windows,
    make_idempotency_key,
    next_cron_times,
    parse_idempotency_key,
)


class TestIdempotencyKey:
    """幂等键生成与反解。"""

    def test_make_key(self) -> None:
        """生成幂等键：{job_id}:{scheduled_at ISO8601}。"""
        job_id = "analysis_1h"
        scheduled_at = datetime(2026, 11, 28, 12, 0, 0, tzinfo=UTC)
        key = make_idempotency_key(job_id, scheduled_at)
        assert key == "analysis_1h:2026-11-28T12:00:00+00:00"

    def test_parse_key(self) -> None:
        """反解幂等键。"""
        key = "analysis_1h:2026-11-28T12:00:00+00:00"
        job_id, scheduled_at = parse_idempotency_key(key)
        assert job_id == "analysis_1h"
        assert scheduled_at.year == 2026
        assert scheduled_at.month == 11
        assert scheduled_at.day == 28
        assert scheduled_at.hour == 12


class TestCronParsing:
    """Cron 表达式解析与触发时间计算。"""

    def test_every_hour(self) -> None:
        """每小时第 5 分钟（`5 * * * *`）。"""
        after = datetime(2026, 11, 28, 10, 0, 0, tzinfo=UTC)
        times = next_cron_times("5 * * * *", after=after, max_count=3)
        assert len(times) == 3
        assert times[0] == datetime(2026, 11, 28, 10, 5, 0, tzinfo=UTC)
        assert times[1] == datetime(2026, 11, 28, 11, 5, 0, tzinfo=UTC)
        assert times[2] == datetime(2026, 11, 28, 12, 5, 0, tzinfo=UTC)

    def test_every_15_minutes(self) -> None:
        """每 15 分钟（`*/15 * * * *`）。"""
        after = datetime(2026, 11, 28, 10, 0, 0, tzinfo=UTC)
        times = next_cron_times("*/15 * * * *", after=after, max_count=5)
        assert len(times) >= 4
        assert times[0] == datetime(2026, 11, 28, 10, 15, 0, tzinfo=UTC)
        assert times[1] == datetime(2026, 11, 28, 10, 30, 0, tzinfo=UTC)

    def test_every_4_hours(self) -> None:
        """每 4 小时第 7 分钟（`7 */4 * * *`）。"""
        # 搜索从 after 的下一分钟开始，00:07 是 00:00 之后的第一个匹配点
        after = datetime(2026, 11, 28, 0, 0, 0, tzinfo=UTC)
        times = next_cron_times("7 */4 * * *", after=after, max_count=5)
        # 00:07, 04:07, 08:07, 12:07, 16:07
        assert times[0] == datetime(2026, 11, 28, 0, 7, 0, tzinfo=UTC)
        assert times[1] == datetime(2026, 11, 28, 4, 7, 0, tzinfo=UTC)
        assert times[2] == datetime(2026, 11, 28, 8, 7, 0, tzinfo=UTC)

    def test_daily_at_8am(self) -> None:
        """每日 08:00 UTC（`0 8 * * *`）。"""
        after = datetime(2026, 11, 28, 10, 0, 0, tzinfo=UTC)
        times = next_cron_times("0 8 * * *", after=after, max_count=3)
        assert len(times) >= 2
        # 次日 08:00
        assert times[0] == datetime(2026, 11, 29, 8, 0, 0, tzinfo=UTC)
        # 大后天 08:00
        if len(times) > 1:
            assert times[1] == datetime(2026, 11, 30, 8, 0, 0, tzinfo=UTC)

    def test_invalid_cron(self) -> None:
        """无效的 cron 表达式。"""
        with pytest.raises(ValueError):
            next_cron_times("5 * * *", after=datetime.now(tz=UTC))  # 仅 4 字段


class TestBackfillCompute:
    """补跑窗口计算。"""

    def test_no_backfill_recent_job(self) -> None:
        """最近执行过的任务，无需补跑。"""
        job = JobSchedule(
            job_id="analysis_1h",
            job_type="CRON",
            cron_expr="5 * * * *",
            interval_seconds=None,
            timezone="UTC",
            max_backfill_window_seconds=86400,
            enabled=True,
        )
        now = datetime(2026, 11, 28, 12, 10, 0, tzinfo=UTC)
        # 10:05 刚执行过
        last_success = datetime(2026, 11, 28, 10, 5, 0, tzinfo=UTC)
        windows, _missed = compute_backfill_windows(job, now=now, last_success_at=last_success)

        # 应该只有 11:05 和 12:05 这两次（距 now 足够近）
        # 但 12:05 在未来，不统计
        assert len(windows) >= 1
        assert all(w.scheduled_at <= now for w in windows)

    def test_missed_beyond_window(self) -> None:
        """超出补跑上限的窗口被标记为 MISSED。"""
        job = JobSchedule(
            job_id="analysis_1h",
            job_type="CRON",
            cron_expr="5 * * * *",
            interval_seconds=None,
            timezone="UTC",
            max_backfill_window_seconds=3600,  # 1 小时
            enabled=True,
        )
        now = datetime(2026, 11, 28, 14, 0, 0, tzinfo=UTC)
        # 2 小时前最后成功
        last_success = datetime(2026, 11, 28, 12, 5, 0, tzinfo=UTC)
        windows, _missed = compute_backfill_windows(job, now=now, last_success_at=last_success)

        # 12:05 已成功执行（即 last_success），搜索从其之后开始，不应被计为 MISSED。
        # 13:05 距 now 55 分钟，在 1h 补跑上限内，应进入 windows 且为补跑。
        assert _missed == []
        assert [w.scheduled_at.hour for w in windows] == [13]
        assert windows[0].is_backfill is True

    def test_backfill_flag_within_window(self) -> None:
        """补跑标记：距当前超过 1 分钟但在补跑窗口内。"""
        job = JobSchedule(
            job_id="analysis_15m",
            job_type="CRON",
            cron_expr="*/15 * * * *",
            interval_seconds=None,
            timezone="UTC",
            max_backfill_window_seconds=86400,
            enabled=True,
        )
        now = datetime(2026, 11, 28, 12, 20, 0, tzinfo=UTC)
        # 2 小时前最后成功
        last_success = datetime(2026, 11, 28, 10, 15, 0, tzinfo=UTC)
        windows, _missed = compute_backfill_windows(job, now=now, last_success_at=last_success)

        # 10:30, 10:45, 11:00, 11:15, 11:30, 11:45, 12:00, 12:15 都应该在补跑范围
        assert len(windows) > 0
        # 除最后一个（刚到点的）外，都应该是 backfill
        for w in windows[:-1]:
            assert w.is_backfill is True


class TestCronFieldMatching:
    """Cron 字段匹配规则。"""

    def test_wildcard(self) -> None:
        """* 匹配任意值。"""
        from aicrypto.services.scheduler_core import _matches_field

        assert _matches_field(0, "*", 0, 59)
        assert _matches_field(30, "*", 0, 59)
        assert _matches_field(59, "*", 0, 59)

    def test_single_value(self) -> None:
        """单个数字。"""
        from aicrypto.services.scheduler_core import _matches_field

        assert _matches_field(5, "5", 0, 59)
        assert not _matches_field(6, "5", 0, 59)

    def test_range(self) -> None:
        """范围 a-b。"""
        from aicrypto.services.scheduler_core import _matches_field

        assert _matches_field(10, "5-15", 0, 59)
        assert _matches_field(5, "5-15", 0, 59)
        assert _matches_field(15, "5-15", 0, 59)
        assert not _matches_field(16, "5-15", 0, 59)

    def test_step(self) -> None:
        """步长 */N。"""
        from aicrypto.services.scheduler_core import _matches_field

        assert _matches_field(0, "*/15", 0, 59)
        assert _matches_field(15, "*/15", 0, 59)
        assert _matches_field(30, "*/15", 0, 59)
        assert _matches_field(45, "*/15", 0, 59)
        assert not _matches_field(10, "*/15", 0, 59)

    def test_list(self) -> None:
        """列表 a,b,c。"""
        from aicrypto.services.scheduler_core import _matches_field

        assert _matches_field(5, "5,10,15", 0, 59)
        assert _matches_field(10, "5,10,15", 0, 59)
        assert not _matches_field(7, "5,10,15", 0, 59)

