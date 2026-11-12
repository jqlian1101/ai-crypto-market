"""快照防未来数据泄漏测试（PRD 第 4.8 / 36.3 必测清单）。

不变量：**快照不含未来数据** —— ``content`` 中所有时间戳 <= ``as_of``。
"""

from __future__ import annotations

import datetime as dt

import pytest

from aicrypto.domain.analysis.snapshot import (
    SnapshotContent,
    assert_no_future_data,
    find_future_timestamps,
)

UTC = dt.timezone.utc
AS_OF = dt.datetime(2026, 11, 12, 0, 0, tzinfo=UTC)


class TestFutureTimestampDetection:
    def test_no_future_data_passes(self) -> None:
        content = {
            "current_price": {"price": "50000", "as_of": "2026-11-11T23:00:00Z"},
            "indicators": {"rsi_14": 55.2},
        }
        assert find_future_timestamps(content, AS_OF) == []
        assert_no_future_data(content, AS_OF)

    def test_future_timestamp_detected(self) -> None:
        content = {
            "current_price": {"price": "50000", "as_of": "2026-11-12T01:00:00Z"},
        }
        violations = find_future_timestamps(content, AS_OF)
        assert len(violations) == 1
        assert "as_of" in violations[0][0]

    def test_future_timestamp_in_nested_list_detected(self) -> None:
        content = {
            "multi_timeframe_ohlcv": {
                "1h": [{"close_time": "2026-11-12T02:00:00Z", "close": "51000"}]
            }
        }
        violations = find_future_timestamps(content, AS_OF)
        assert len(violations) == 1

    def test_timestamp_equal_to_as_of_is_allowed(self) -> None:
        content = {"current_price": {"as_of": "2026-11-12T00:00:00Z"}}
        assert find_future_timestamps(content, AS_OF) == []

    def test_assert_raises_on_future_data(self) -> None:
        content = {"current_price": {"as_of": "2026-11-12T05:00:00Z"}}
        with pytest.raises(ValueError, match="as_of 之后的数据"):
            assert_no_future_data(content, AS_OF)


class TestAgentViews:
    """24.4：各自独立快照视图，控制 token 预算。"""

    def test_technical_view_excludes_derivatives(self) -> None:
        content = SnapshotContent(
            current_price={"price": "50000"},
            indicators={"rsi_14": 55},
            derivatives={"open_interest": "100"},
            liquidity={"spread_bps": "0.5"},
        )
        trimmed = content.trimmed_for("technical")
        assert "indicators" in trimmed
        assert "derivatives" not in trimmed
        assert "liquidity" not in trimmed

    def test_master_view_includes_derivatives(self) -> None:
        content = SnapshotContent(
            current_price={"price": "50000"},
            derivatives={"open_interest": "100"},
        )
        trimmed = content.trimmed_for("master")
        assert "derivatives" in trimmed

    def test_unknown_view_returns_full_content(self) -> None:
        content = SnapshotContent(current_price={"price": "1"})
        trimmed = content.trimmed_for("unknown_agent")
        assert trimmed == content.to_dict()

    def test_risk_view_includes_account_positions(self) -> None:
        content = SnapshotContent(
            current_price={"price": "50000"},
            account_and_positions={"equity": "1000"},
        )
        trimmed = content.trimmed_for("risk")
        assert "account_and_positions" in trimmed
