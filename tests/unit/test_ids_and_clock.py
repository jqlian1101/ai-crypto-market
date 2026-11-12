"""ID 生成与时钟测试（PRD 第 30.1 / 36.1.3）。"""

from __future__ import annotations

import datetime as dt

import pytest

from aicrypto.common.clock import FrozenClock, from_iso, to_iso
from aicrypto.common.ids import (
    new_anomaly_event_id,
    new_id,
    new_notification_id,
    new_output_id,
    new_prediction_id,
    new_run_id,
    new_signal_id,
    new_snapshot_id,
)

UTC = dt.timezone.utc
MOMENT = dt.datetime(2026, 11, 12, 8, 30, tzinfo=UTC)


class TestIdPrefixes:
    """30.1 的 ID 前缀约定。"""

    def test_snapshot_id_format(self) -> None:
        assert new_snapshot_id(now=MOMENT).startswith("snap_20261112_")

    def test_run_id_format(self) -> None:
        assert new_run_id(now=MOMENT).startswith("run_20261112_")

    def test_plain_ids(self) -> None:
        assert new_output_id().startswith("out_")
        assert new_signal_id().startswith("sig_")
        assert new_prediction_id().startswith("pred_")
        assert new_notification_id().startswith("ne_")

    def test_anomaly_event_id_format(self) -> None:
        event_id = new_anomaly_event_id(now=MOMENT, sequence=7)
        assert event_id == "ae_20261112_0007"

    def test_unregistered_prefix_raises(self) -> None:
        with pytest.raises(ValueError, match="未登记的 ID 前缀"):
            new_id("bogus")

    def test_ids_are_unique(self) -> None:
        ids = {new_snapshot_id(now=MOMENT) for _ in range(100)}
        assert len(ids) == 100


class TestClock:
    def test_to_iso_utc_seconds(self) -> None:
        moment = dt.datetime(2026, 11, 9, 1, 23, 45, 123456, tzinfo=UTC)
        assert to_iso(moment) == "2026-11-09T01:23:45Z"

    def test_to_iso_converts_timezone(self) -> None:
        shanghai = dt.timezone(dt.timedelta(hours=8))
        moment = dt.datetime(2026, 11, 9, 9, 23, 45, tzinfo=shanghai)
        assert to_iso(moment) == "2026-11-09T01:23:45Z"

    def test_from_iso_roundtrip(self) -> None:
        assert from_iso("2026-11-09T01:23:45Z") == dt.datetime(
            2026, 11, 9, 1, 23, 45, tzinfo=UTC
        )

    def test_frozen_clock_advance(self) -> None:
        clock = FrozenClock(MOMENT)
        clock.advance(dt.timedelta(hours=1))
        assert clock.now() == dt.datetime(2026, 11, 12, 9, 30, tzinfo=UTC)
