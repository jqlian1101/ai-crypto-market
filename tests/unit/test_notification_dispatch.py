"""异动自动投递编排测试（PRD 22.4.4.1）。

重点验证状态机：只有投递成功才写去重/冷却键；失败不写，保证可重试。
投递与 Redis 以 monkeypatch 替换，不依赖外部服务。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from aicrypto.services import notification_dispatch as dispatch


@dataclass
class FakeSession:
    _rows: list[Any] = field(default_factory=list)
    commits: int = 0

    def scalars(self, _stmt: Any) -> list[Any]:
        return list(self._rows)

    def commit(self) -> None:
        self.commits += 1


@dataclass(frozen=True)
class Outcome:
    status: str
    error: str | None = None


def _event(fp: str) -> SimpleNamespace:
    return SimpleNamespace(
        anomaly_event_id=f"evt_{fp}",
        fingerprint=fp,
        cooldown_key=f"BTCUSDT:{fp}",
        severity="WARN",
        detected_at=dt.datetime(2026, 11, 20, tzinfo=dt.UTC),
        notification_status="PENDING",
    )


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    events: list[SimpleNamespace],
    outcomes: dict[str, Outcome],
) -> tuple[dict[str, int], list[str], FakeSession]:
    marked: list[str] = []

    async def fake_deliver(db: Any, redis: Any, event: Any, *, settings: Any) -> Outcome:
        return outcomes[event.fingerprint]

    async def fake_mark(redis: Any, *, fingerprint: str, **_: Any) -> None:
        marked.append(fingerprint)

    monkeypatch.setattr(dispatch, "deliver_anomaly", fake_deliver)
    monkeypatch.setattr(dispatch, "mark_delivered", fake_mark)

    session = FakeSession(_rows=events)
    counts = await dispatch.deliver_pending_anomalies(
        session,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        settings=object(),  # type: ignore[arg-type]
    )
    return counts, marked, session


async def test_success_marks_sent_and_writes_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    ev = _event("fp_ok")
    counts, marked, _ = await _run(monkeypatch, [ev], {"fp_ok": Outcome("SUCCESS")})

    assert ev.notification_status == "SENT"
    assert counts == {"SENT": 1, "SUPPRESSED": 0, "FAILED": 0}
    assert marked == ["fp_ok"]


async def test_failure_marks_failed_and_does_not_write_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失败不得写去重/冷却键，否则会永久吞掉这条告警（PRD 22.4.4.1）。"""
    ev = _event("fp_fail")
    counts, marked, _ = await _run(
        monkeypatch, [ev], {"fp_fail": Outcome("FAILED", error="飞书 HTTP 500")}
    )

    assert ev.notification_status == "FAILED"
    assert counts["FAILED"] == 1
    assert marked == []


async def test_dedup_hit_marks_suppressed_without_redis_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ev = _event("fp_dup")
    counts, marked, _ = await _run(monkeypatch, [ev], {"fp_dup": Outcome("SKIPPED")})

    assert ev.notification_status == "SUPPRESSED"
    assert counts["SUPPRESSED"] == 1
    assert marked == []


async def test_one_failure_does_not_block_rest_of_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [_event("a"), _event("b"), _event("c")]
    outcomes = {
        "a": Outcome("SUCCESS"),
        "b": Outcome("FAILED", error="x"),
        "c": Outcome("SUCCESS"),
    }
    counts, marked, session = await _run(monkeypatch, events, outcomes)

    assert counts == {"SENT": 2, "SUPPRESSED": 0, "FAILED": 1}
    assert marked == ["a", "c"]
    assert session.commits == 3  # 逐条提交，单条失败不影响其余
