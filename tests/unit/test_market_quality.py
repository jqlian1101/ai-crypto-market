"""行情数据质量不变量测试（PRD 第 36.3 必测清单）。

覆盖：

- 未收盘 K 线不落库（29.5）
- K 线连续性无缺口（29.4.1）
- OHLC 合法性（4.3）
- 主动买卖方向正确（30.2）
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from aicrypto.domain.market.quality import (
    Candle,
    DataQuality,
    Severity,
    align_to_timeframe,
    evaluate_staleness,
    find_gaps,
    gap_severity,
    is_candle_closed,
    is_taker_buy,
    validate_candle,
    validate_ohlc,
)

UTC = dt.UTC


def make_candle(
    *,
    open_time: dt.datetime,
    timeframe: str = "1h",
    open_: str = "100",
    high: str = "105",
    low: str = "95",
    close: str = "102",
    volume: str = "10",
    quote_volume: str = "1000",
) -> Candle:
    step = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}[timeframe]
    return Candle(
        symbol="BTCUSDT",
        market_type="SPOT",
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + dt.timedelta(seconds=step),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        quote_volume=Decimal(quote_volume),
    )


class TestClosedCandleOnly:
    """29.5.1：未收盘 K 线不落库。"""

    def test_unclosed_candle_is_not_closed(self, frozen_clock) -> None:
        candle = make_candle(open_time=dt.datetime(2026, 11, 12, 0, 0, tzinfo=UTC))
        # 冻结时钟在 2026-11-12 00:00:00，此时该 1h K 线刚好开盘
        assert not is_candle_closed(candle)

    def test_closed_candle_is_closed(self, frozen_clock) -> None:
        candle = make_candle(open_time=dt.datetime(2026, 11, 11, 23, 0, tzinfo=UTC))
        assert is_candle_closed(candle)

    def test_unclosed_candle_produces_no_quality_event(self, frozen_clock) -> None:
        """未收盘是正常流程（等待收盘），不是质量问题，不应刷事件。"""
        candle = make_candle(open_time=dt.datetime(2026, 11, 12, 0, 0, tzinfo=UTC))
        events = validate_candle(candle)
        assert events == []

    def test_closed_candle_with_valid_ohlc_produces_no_event(self, frozen_clock) -> None:
        candle = make_candle(open_time=dt.datetime(2026, 11, 11, 23, 0, tzinfo=UTC))
        assert validate_candle(candle) == []


class TestOhlcValidation:
    """4.3：OHLC 合法性。"""

    def test_valid_candle_passes(self) -> None:
        candle = make_candle(open_time=dt.datetime(2026, 11, 11, 23, 0, tzinfo=UTC))
        assert validate_ohlc(candle) is None

    def test_high_below_low_rejected(self) -> None:
        candle = make_candle(
            open_time=dt.datetime(2026, 11, 11, 23, 0, tzinfo=UTC),
            high="90",
            low="95",
        )
        error = validate_ohlc(candle)
        assert error is not None
        assert "low" in error

    def test_high_below_close_rejected(self) -> None:
        candle = make_candle(
            open_time=dt.datetime(2026, 11, 11, 23, 0, tzinfo=UTC),
            high="101",
            close="102",
        )
        error = validate_ohlc(candle)
        assert error is not None
        assert "max" in error

    def test_negative_volume_rejected(self) -> None:
        candle = make_candle(
            open_time=dt.datetime(2026, 11, 11, 23, 0, tzinfo=UTC),
            volume="-1",
        )
        assert validate_ohlc(candle) is not None

    def test_invalid_candle_produces_critical_event(self, frozen_clock) -> None:
        candle = make_candle(
            open_time=dt.datetime(2026, 11, 11, 23, 0, tzinfo=UTC),
            high="90",
            low="95",
        )
        events = validate_candle(candle)
        assert len(events) == 1
        assert events[0].severity is Severity.CRITICAL
        assert events[0].data_quality is DataQuality.INVALID
        assert events[0].rule_id == "DATA_OHLC_INVALID"


class TestGapDetection:
    """29.4.1：K 线连续性无缺口。"""

    def test_contiguous_candles_have_no_gap(self) -> None:
        base = dt.datetime(2026, 11, 11, 0, 0, tzinfo=UTC)
        candles = [
            make_candle(open_time=base + dt.timedelta(hours=i), timeframe="1h") for i in range(5)
        ]
        assert find_gaps(candles, symbol="BTCUSDT", market_type="SPOT", timeframe="1h") == []

    def test_missing_candle_detected(self) -> None:
        base = dt.datetime(2026, 11, 11, 0, 0, tzinfo=UTC)
        candles = [
            make_candle(open_time=base + dt.timedelta(hours=i), timeframe="1h")
            for i in (0, 1, 3, 4)
        ]
        gaps = find_gaps(candles, symbol="BTCUSDT", market_type="SPOT", timeframe="1h")
        assert len(gaps) == 1
        assert gaps[0]["missing_bars"] == 1

    def test_gap_severity_thresholds(self) -> None:
        assert gap_severity(1) is Severity.WARN
        assert gap_severity(3) is Severity.CRITICAL
        assert gap_severity(0) is Severity.INFO


class TestTakerBuyDirection:
    """30.2：主动买卖方向。"""

    @pytest.mark.parametrize(
        ("is_buyer_maker", "expected"),
        [(True, False), (False, True)],
    )
    def test_is_taker_buy_is_negation_of_is_buyer_maker(
        self, is_buyer_maker: bool, expected: bool
    ) -> None:
        assert is_taker_buy(is_buyer_maker) is expected

    def test_buyer_maker_means_aggressive_sell(self) -> None:
        """Binance: isBuyerMaker=true 表示买方是挂单方 → 主动卖出。"""
        assert is_taker_buy(True) is False


class TestAlignment:
    def test_align_to_timeframe(self) -> None:
        moment = dt.datetime(2026, 11, 12, 10, 7, 33, tzinfo=UTC)
        assert align_to_timeframe(moment, "15m") == dt.datetime(2026, 11, 12, 10, 0, tzinfo=UTC)

    def test_align_1h(self) -> None:
        moment = dt.datetime(2026, 11, 12, 10, 59, 59, tzinfo=UTC)
        assert align_to_timeframe(moment, "1h") == dt.datetime(2026, 11, 12, 10, 0, tzinfo=UTC)


class TestStaleness:
    def test_fresh_data_has_no_event(self, frozen_clock) -> None:
        last = dt.datetime(2026, 11, 12, 0, 0, tzinfo=UTC)
        assert evaluate_staleness(last) is None

    def test_stale_data_produces_event(self, frozen_clock) -> None:
        last = dt.datetime(2026, 11, 11, 23, 0, tzinfo=UTC)
        event = evaluate_staleness(last)
        assert event is not None
        assert event.rule_id == "DATA_STALE"
        assert event.data_quality is DataQuality.STALE
