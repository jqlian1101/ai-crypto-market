"""行情入库纯判定测试（PRD 29.4.2 / 29.5.1）。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from aicrypto.infrastructure.binance_ws import KlineEvent
from aicrypto.services import market_ingest as mi

UTC = dt.UTC
OPEN = dt.datetime(2026, 1, 1, tzinfo=UTC)


def make_event(*, is_closed: bool, timeframe: str = "5m") -> KlineEvent:
    row: dict[str, Any] = {
        "exchange": "binance",
        "market_type": "USDM_FUTURES",
        "symbol": "BTCUSDT",
        "timeframe": timeframe,
        "open_time": OPEN,
        "close_time": OPEN + dt.timedelta(minutes=5),
        "close": Decimal("101.2"),
        "is_closed": is_closed,
        "source": "WS",
    }
    return KlineEvent(
        market_type="USDM_FUTURES",
        symbol="BTCUSDT",
        timeframe=timeframe,
        is_closed=is_closed,
        exchange_ts=OPEN,
        row=row,
    )


def test_route_closed_to_persist() -> None:
    assert mi.route_event(make_event(is_closed=True)) is mi.IngestAction.PERSIST


def test_route_unclosed_to_cache_never_persist() -> None:
    assert mi.route_event(make_event(is_closed=False)) is mi.IngestAction.CACHE_UNCLOSED


def test_unclosed_cache_key_normalizes_symbol() -> None:
    assert mi.unclosed_cache_key("SPOT", "btcusdt", "1h") == "market:unclosed:SPOT:BTCUSDT:1h"


@pytest.mark.parametrize(
    ("timeframe", "ttl"),
    [("1m", 120), ("5m", 600), ("15m", 1800), ("1h", 7200), ("4h", 28800)],
)
def test_unclosed_ttl_is_twice_interval(timeframe: str, ttl: int) -> None:
    assert mi.unclosed_ttl_seconds(timeframe) == ttl


def test_same_close_is_not_divergence() -> None:
    res = mi.resolve_closed_conflict(
        existing_source="WS",
        existing_close=Decimal("100"),
        incoming_source="REST",
        incoming_close=Decimal("100.0"),
    )
    assert res == mi.Resolution(overwrite=False, divergent=False)


def test_rest_overrides_ws_on_divergence() -> None:
    res = mi.resolve_closed_conflict(
        existing_source="WS",
        existing_close=Decimal("100"),
        incoming_source="REST_BACKFILL",
        incoming_close=Decimal("101"),
    )
    assert res == mi.Resolution(overwrite=True, divergent=True)


def test_ws_does_not_override_existing_on_divergence() -> None:
    res = mi.resolve_closed_conflict(
        existing_source="REST",
        existing_close=Decimal("100"),
        incoming_source="WS",
        incoming_close=Decimal("101"),
    )
    assert res == mi.Resolution(overwrite=False, divergent=True)


def test_divergence_event_is_warn_degraded_crosscheck() -> None:
    event = mi.build_divergence_event(
        market_type="SPOT",
        symbol="BTCUSDT",
        timeframe="1h",
        open_time=OPEN,
        kept_source="WS",
        kept_close=Decimal("100"),
        incoming_source="REST",
        incoming_close=Decimal("101"),
        detected_at=OPEN,
    )
    assert event.rule_id == "DATA_SOURCE_DIVERGENCE"
    assert event.severity == "WARN"
    assert event.data_quality == "DEGRADED"
    assert event.detected_by == "CROSSCHECK"
    assert event.id.startswith("dqe_")
    assert event.context is not None
    assert event.context["authoritative"] == "REST"
    assert event.context["incoming_close"] == "101"


async def test_cache_unclosed_writes_with_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, int]] = []

    async def fake_set(key: str, value: str, *, ttl_seconds: int) -> None:
        calls.append((key, value, ttl_seconds))

    monkeypatch.setattr(mi, "cache_set", fake_set)
    key = await mi.cache_unclosed(make_event(is_closed=False, timeframe="1h"))
    assert key == "market:unclosed:USDM_FUTURES:BTCUSDT:1h"
    assert len(calls) == 1
    _, payload, ttl = calls[0]
    assert ttl == 7200
    assert '"is_closed": false' in payload
    assert '"close": "101.2"' in payload
