"""可注入时钟（PRD 第 36.1.3 条）。

**任何涉及时间的功能必须可注入时钟**，禁止直接调用 ``datetime.now(timezone.utc)``。
本模块提供全局 :data:`clock` 与 :func:`set_clock` / :func:`reset_clock`，
以及 FastAPI 依赖 :func:`get_now`。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Protocol

UTC = dt.UTC


class Clock(Protocol):
    """时钟协议。"""

    def now(self) -> dt.datetime:
        """返回带时区的 UTC 时间。"""
        ...


class SystemClock:
    """真实系统时钟。"""

    def now(self) -> dt.datetime:
        return dt.datetime.now(UTC)


class FrozenClock:
    """固定时钟，测试用。"""

    def __init__(self, moment: dt.datetime) -> None:
        self._moment = moment

    def now(self) -> dt.datetime:
        return self._moment

    def advance(self, delta: dt.timedelta) -> None:
        self._moment += delta

    def set(self, moment: dt.datetime) -> None:
        self._moment = moment


_clock: Clock = SystemClock()


def get_clock() -> Clock:
    return _clock


def set_clock(new_clock: Clock) -> None:
    """替换全局时钟（测试用）。"""
    global _clock
    _clock = new_clock


def reset_clock() -> None:
    """恢复为系统时钟。"""
    global _clock
    _clock = SystemClock()


def now() -> dt.datetime:
    """当前 UTC 时间。业务代码统一从这里取时间。"""
    return _clock.now()


def now_iso() -> str:
    """当前 UTC 时间的 ISO-8601 秒精度字符串（31.1）。"""
    return to_iso(now())


def to_iso(moment: dt.datetime) -> str:
    """格式化为 ISO-8601 UTC 秒精度（``2026-11-09T01:23:45Z``）。"""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def from_iso(value: str) -> dt.datetime:
    """解析 ISO-8601 字符串为带时区 UTC 时间。"""
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


ClockFactory = Callable[[], Clock]
