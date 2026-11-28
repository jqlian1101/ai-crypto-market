"""异动 CRITICAL → 分析入队的纪律（PRD 22.6）。

用例锁定：

- 只有 CRITICAL 入队，INFO / WARN 不入队；
- 幂等键由事件 ID 派生，同一事件重复入队得到同一键；
- 入队的 trigger 为 ``ANOMALY``，市场为 ``USDM_FUTURES``。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from aicrypto.domain.anomaly.identity import build_event_id
from aicrypto.services import anomaly_analysis
from aicrypto.services.anomaly_analysis import (
    anomaly_idempotency_key,
    enqueue_critical_analyses,
)

NOW = dt.datetime(2026, 11, 28, 12, 0, tzinfo=dt.UTC)


@dataclass(frozen=True)
class _Event:
    fingerprint: str
    severity: str


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_create_run(db, **kwargs):
        calls.append(kwargs)
        return {"run_id": f"run_{len(calls)}"}

    monkeypatch.setattr(anomaly_analysis, "create_run", fake_create_run)
    return calls


def test_only_critical_events_enqueue(captured: list[dict]) -> None:
    events = [
        _Event(fingerprint="fp_info", severity="INFO"),
        _Event(fingerprint="fp_warn", severity="WARN"),
        _Event(fingerprint="fp_crit", severity="CRITICAL"),
    ]
    run_ids = enqueue_critical_analyses(None, symbol="BTCUSDT", events=events, now=NOW)

    assert run_ids == ["run_1"]
    assert len(captured) == 1


def test_idempotency_key_is_derived_from_event_id(captured: list[dict]) -> None:
    event = _Event(fingerprint="fp_crit", severity="CRITICAL")
    enqueue_critical_analyses(None, symbol="BTCUSDT", events=[event], now=NOW)

    expected = anomaly_idempotency_key(build_event_id("fp_crit", 0))
    assert captured[0]["idempotency_key"] == expected


def test_same_event_yields_same_key_on_repeat(captured: list[dict]) -> None:
    event = _Event(fingerprint="fp_crit", severity="CRITICAL")
    enqueue_critical_analyses(None, symbol="BTCUSDT", events=[event], now=NOW)
    enqueue_critical_analyses(None, symbol="BTCUSDT", events=[event], now=NOW)

    assert captured[0]["idempotency_key"] == captured[1]["idempotency_key"]


def test_trigger_and_market_are_anomaly_and_futures(captured: list[dict]) -> None:
    event = _Event(fingerprint="fp_crit", severity="CRITICAL")
    enqueue_critical_analyses(None, symbol="BTCUSDT", events=[event], now=NOW)

    assert captured[0]["trigger"] == "ANOMALY"
    assert captured[0]["market_type"] == "USDM_FUTURES"
    assert captured[0]["analysis_mode"] == "LIVE"


def test_no_events_enqueues_nothing(captured: list[dict]) -> None:
    assert enqueue_critical_analyses(None, symbol="BTCUSDT", events=[], now=NOW) == []
    assert captured == []
