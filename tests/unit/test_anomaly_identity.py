"""异动身份、指纹与冷却键测试（PRD 22.4.4.1）。"""

from __future__ import annotations

import datetime as dt

import pytest

from aicrypto.domain.anomaly.identity import (
    build_cooldown_key,
    build_event_id,
    build_fingerprint,
)

T = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)


def test_fingerprint_is_deterministic() -> None:
    a = build_fingerprint(symbol="BTCUSDT", market_type="USDM_FUTURES", rule_id="X", window_start=T)
    b = build_fingerprint(symbol="BTCUSDT", market_type="USDM_FUTURES", rule_id="X", window_start=T)
    assert a == b
    assert a.startswith("ae_")


def test_fingerprint_differs_by_window_and_rule() -> None:
    base = build_fingerprint(
        symbol="BTCUSDT", market_type="USDM_FUTURES", rule_id="X", window_start=T
    )
    later = build_fingerprint(
        symbol="BTCUSDT",
        market_type="USDM_FUTURES",
        rule_id="X",
        window_start=T + dt.timedelta(minutes=5),
    )
    other_rule = build_fingerprint(
        symbol="BTCUSDT", market_type="USDM_FUTURES", rule_id="Y", window_start=T
    )
    assert base != later
    assert base != other_rule


def test_fingerprint_normalises_timezone() -> None:
    cst = dt.timezone(dt.timedelta(hours=8))
    as_utc = build_fingerprint(
        symbol="BTCUSDT", market_type="USDM_FUTURES", rule_id="X", window_start=T
    )
    as_cst = build_fingerprint(
        symbol="BTCUSDT",
        market_type="USDM_FUTURES",
        rule_id="X",
        window_start=T.astimezone(cst),
    )
    assert as_utc == as_cst


def test_fingerprint_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="时区"):
        build_fingerprint(
            symbol="BTCUSDT",
            market_type="USDM_FUTURES",
            rule_id="X",
            window_start=dt.datetime(2026, 11, 20, 12, 0),
        )


def test_event_id_distinct_per_revision_but_shares_fingerprint() -> None:
    fp = "ae_abc"
    assert build_event_id(fp, 0) != build_event_id(fp, 1)
    assert build_event_id(fp, 1).startswith(fp)


def test_event_id_rejects_negative_revision() -> None:
    with pytest.raises(ValueError):
        build_event_id("ae_abc", -1)


def test_cooldown_key_format() -> None:
    assert build_cooldown_key(symbol="BTCUSDT", rule_id="PRICE_PCT_5M_CRIT") == (
        "BTCUSDT:PRICE_PCT_5M_CRIT"
    )
