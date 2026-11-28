"""去重与冷却判定测试（PRD 22.4.4 / 22.4.4.1）。"""

from __future__ import annotations

import datetime as dt

import pytest

from aicrypto.domain.anomaly.dedup import (
    COOLDOWN_CRITICAL,
    COOLDOWN_WARN,
    cooldown_window,
    decide_delivery,
    dedup_key,
)

NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)
KEY = "BTCUSDT:PRICE_PCT_5M_CRIT"
FP = "ae_abc"


def _decide(**overrides):  # type: ignore[no-untyped-def]
    args = {
        "fingerprint": FP,
        "revision": 0,
        "severity": "CRITICAL",
        "cooldown_key": KEY,
        "now": NOW,
        "delivered_keys": set(),
        "last_fired_at": {},
    }
    args.update(overrides)
    return decide_delivery(**args)


def test_first_delivery_allowed() -> None:
    d = _decide()
    assert d.allowed
    assert d.reason == "OK"


def test_same_fingerprint_revision_blocked_by_dedup() -> None:
    d = _decide(delivered_keys={dedup_key(FP, 0)})
    assert not d.allowed
    assert d.reason == "DEDUP_HIT"


def test_revision_one_allowed_for_same_fingerprint() -> None:
    """22.4.4.1：同一事件的 AI 解读（revision=1）必须能送达。"""
    d = _decide(revision=1, delivered_keys={dedup_key(FP, 0)}, last_fired_at={KEY: NOW})
    assert d.allowed


def test_cooldown_blocks_new_event_same_key() -> None:
    last = NOW - dt.timedelta(minutes=1)
    d = _decide(last_fired_at={KEY: last})
    assert not d.allowed
    assert d.reason == "COOLDOWN"


def test_cooldown_expires_after_window() -> None:
    last = NOW - COOLDOWN_CRITICAL - dt.timedelta(seconds=1)
    assert _decide(last_fired_at={KEY: last}).allowed


def test_warn_uses_longer_cooldown() -> None:
    """10 分钟前触发：CRITICAL（5 分钟）已过冷却，WARN（30 分钟）仍在冷却中。"""
    last = NOW - dt.timedelta(minutes=10)
    assert not _decide(severity="WARN", last_fired_at={KEY: last}).allowed
    assert _decide(severity="CRITICAL", last_fired_at={KEY: last}).allowed
    assert cooldown_window("WARN") == COOLDOWN_WARN


def test_warn_cooldown_expires_after_30m() -> None:
    last = NOW - COOLDOWN_WARN - dt.timedelta(seconds=1)
    assert _decide(severity="WARN", last_fired_at={KEY: last}).allowed


def test_upgrade_delivery_bypasses_cooldown() -> None:
    """22.4.4.1 第 3 条：升级投递不占用冷却窗口，即使仍在冷却期内。"""
    d = _decide(revision=1, last_fired_at={KEY: NOW})
    assert d.allowed


def test_dedup_and_cooldown_are_independent_key_spaces() -> None:
    """去重键与冷却键互不干扰：去重键字符串不应等于冷却键。"""
    assert dedup_key(FP, 0) != KEY
    assert not dedup_key(FP, 0).startswith(KEY)


def test_different_cooldown_key_not_blocked() -> None:
    last = NOW - dt.timedelta(seconds=10)
    assert _decide(cooldown_key="ETHUSDT:PRICE_PCT_5M_CRIT", last_fired_at={KEY: last}).allowed


def test_unknown_severity_rejected() -> None:
    with pytest.raises(ValueError):
        cooldown_window("INFO")


def test_injected_cooldown_seconds_override_defaults() -> None:
    """NOTIFY_COOLDOWN_* 注入后，窗口必须采用注入值而非 PRD 默认值。"""
    assert cooldown_window("WARN", warn_seconds=60) == dt.timedelta(seconds=60)
    assert cooldown_window("CRITICAL", critical_seconds=42) == dt.timedelta(seconds=42)


def test_injected_zero_cooldown_disables_suppression() -> None:
    """配置为 0 表示关闭冷却：刚投过也必须放行。"""
    last = NOW - dt.timedelta(seconds=1)
    d = _decide(severity="WARN", last_fired_at={KEY: last}, warn_seconds=0)
    assert d.allowed


def test_config_flip_changes_decision_for_same_event() -> None:
    """同一事件（10 分钟前触发的 WARN）：默认 30 分钟窗口仍在冷却，
    把 WARN 冷却改成 5 分钟后必须放行。这是「配置真正生效」的直接证据。"""
    last = NOW - dt.timedelta(minutes=10)
    before = _decide(severity="WARN", last_fired_at={KEY: last})
    after = _decide(severity="WARN", last_fired_at={KEY: last}, warn_seconds=300)
    assert before.reason == "COOLDOWN"
    assert after.allowed
