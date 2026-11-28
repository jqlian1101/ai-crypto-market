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
    mark_kwargs: list[dict[str, Any]] = field(default_factory=list)

    def scalars(self, _stmt: Any) -> list[Any]:
        return list(self._rows)

    def commit(self) -> None:
        self.commits += 1


@dataclass(frozen=True)
class Outcome:
    status: str
    error: str | None = None


def _event(fp: str, revision: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        anomaly_event_id=f"evt_{fp}",
        fingerprint=fp,
        revision=revision,
        cooldown_key=f"BTCUSDT:{fp}",
        severity="WARN",
        detected_at=dt.datetime(2026, 11, 20, tzinfo=dt.UTC),
        notification_status="PENDING",
    )


def _settings(critical: int = 300, warn: int = 1800) -> SimpleNamespace:
    """仅含投递编排所需字段的配置替身（冷却秒数必须显式给出，不得依赖默认值）。"""
    return SimpleNamespace(
        NOTIFY_COOLDOWN_CRITICAL_SECONDS=critical,
        NOTIFY_COOLDOWN_WARN_SECONDS=warn,
    )


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    events: list[SimpleNamespace],
    outcomes: dict[str, Outcome],
) -> tuple[dict[str, int], list[str], FakeSession]:
    marked: list[str] = []
    mark_kwargs: list[dict[str, Any]] = []

    async def fake_deliver(db: Any, redis: Any, event: Any, *, settings: Any) -> Outcome:
        return outcomes[event.fingerprint]

    async def fake_mark(redis: Any, *, fingerprint: str, **kwargs: Any) -> None:
        marked.append(fingerprint)
        mark_kwargs.append({"fingerprint": fingerprint, **kwargs})

    monkeypatch.setattr(dispatch, "deliver_anomaly", fake_deliver)
    monkeypatch.setattr(dispatch, "mark_delivered", fake_mark)

    session = FakeSession(_rows=events)
    counts = await dispatch.deliver_pending_anomalies(
        session,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
    )
    session.mark_kwargs = mark_kwargs  # type: ignore[attr-defined]
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


async def test_mark_delivered_uses_event_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    """L-27：写去重键的 revision 必须等于事件的 revision。

    去重键是 ``(fingerprint, revision)``。若写入时固定用 0，
    升级投递（revision>0）会写下一个**永远不会被检查**的键——
    下一轮扫描查不到「已投递」，于是**反复重投**同一事件。
    """
    ev = _event("fp_upgraded", revision=1)
    _, marked, session = await _run(monkeypatch, [ev], {"fp_upgraded": Outcome("SUCCESS")})

    assert marked == ["fp_upgraded"]
    assert len(session.mark_kwargs) == 1
    assert session.mark_kwargs[0]["revision"] == 1


async def test_configured_cooldown_seconds_reach_mark_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NOTIFY_COOLDOWN_* 必须真正传到 Redis TTL 的写入处，而不是被默认值吞掉。"""
    captured: list[dict[str, Any]] = []

    async def fake_deliver(db: Any, redis: Any, event: Any, *, settings: Any) -> Outcome:
        return Outcome("SUCCESS")

    async def fake_mark(redis: Any, **kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(dispatch, "deliver_anomaly", fake_deliver)
    monkeypatch.setattr(dispatch, "mark_delivered", fake_mark)

    await dispatch.deliver_pending_anomalies(
        FakeSession(_rows=[_event("fp_cfg")]),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        settings=_settings(critical=42, warn=777),  # type: ignore[arg-type]
    )

    assert captured[0]["critical_seconds"] == 42
    assert captured[0]["warn_seconds"] == 777


async def test_zero_revision_is_passed_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    """revision=0 也必须显式传递——不给默认值，避免调用方漏传后静默走错键。"""
    ev = _event("fp_zero")
    _, _, session = await _run(monkeypatch, [ev], {"fp_zero": Outcome("SUCCESS")})

    assert session.mark_kwargs[0]["revision"] == 0
