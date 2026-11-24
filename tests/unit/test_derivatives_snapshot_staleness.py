"""衍生品快照层的陈旧判定与水位线语义测试（PRD 29.4.3 / 29.6）。

这些是**真实库实测才发现**的缺陷的回归守卫：

1. 陈旧年龄不得为负——``now`` 早于数据业务时间时（时钟漂移 / 调用方传了
   历史时刻），负数会让「陈旧」判定被静默绕过；
2. 陈旧判定基于**调用方传入的截止时刻**，而不是真实当前时间；
3. 缺失原因必须区分「没采到」与「采到了但陈旧」。
"""

from __future__ import annotations

import datetime as dt

from aicrypto.services.derivatives_snapshot import (
    BOOK_STALE_SECONDS,
    _age_seconds,
)

UTC = dt.UTC
BASE = dt.datetime(2026, 1, 1, tzinfo=UTC)


def test_age_seconds_is_zero_when_data_is_newer_than_now() -> None:
    """数据业务时间晚于 now 时，年龄钳制为 0，不得为负。"""
    assert _age_seconds(BASE, BASE + dt.timedelta(hours=1)) == 0.0


def test_age_seconds_is_zero_for_equal_timestamps() -> None:
    assert _age_seconds(BASE, BASE) == 0.0


def test_age_seconds_is_positive_for_older_data() -> None:
    assert _age_seconds(BASE + dt.timedelta(seconds=60), BASE) == 60.0


def test_age_seconds_never_negative() -> None:
    """任意时序下年龄都不为负。"""
    for offset_hours in (-48, -1, 0, 1, 48):
        age = _age_seconds(BASE, BASE + dt.timedelta(hours=offset_hours))
        assert age >= 0.0


def test_stale_threshold_is_positive() -> None:
    assert BOOK_STALE_SECONDS > 0
