"""行情数据质量规则（PRD 第 4.3 章）。

**关键不变量（36.3 必测清单）**：

1. **未收盘 K 线不落库**（29.5.1）
2. **K 线连续性无缺口**（29.4.1）
3. **OHLC 合法性**（4.3）—— 非法 K 线被拒并产生 ``INVALID`` 事件
4. **主动买卖方向正确**（30.2）—— ``is_taker_buy == NOT is_buyer_maker``
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from aicrypto.common.clock import now as clock_now

#: 各周期的秒数
TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}


class DataQuality(StrEnum):
    """4.3 的五态质量标记。"""

    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    MISSING = "MISSING"
    INVALID = "INVALID"


class Severity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class Candle:
    """K 线的领域表示（与 ORM 解耦，便于纯函数测试）。"""

    symbol: str
    market_type: str
    timeframe: str
    open_time: dt.datetime
    close_time: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trades_count: int = 0
    taker_buy_base_volume: Decimal | None = None
    taker_buy_quote_volume: Decimal | None = None
    exchange_ts: dt.datetime | None = None
    source: str = "WS"


@dataclass(frozen=True, slots=True)
class QualityEvent:
    """一条数据质量事件。"""

    rule_id: str
    severity: Severity
    data_quality: DataQuality
    detected_by: str
    context: dict[str, object]
    symbol: str | None = None
    market_type: str | None = None
    timeframe: str | None = None


def interval_seconds(timeframe: str) -> int:
    """周期秒数。"""
    try:
        return TIMEFRAME_SECONDS[timeframe]
    except KeyError as exc:
        raise ValueError(f"未知周期: {timeframe!r}") from exc


def align_to_timeframe(moment: dt.datetime, timeframe: str) -> dt.datetime:
    """把时间对齐到周期边界（UTC）。

    例：``15m`` 把 ``10:07:33`` 对齐到 ``10:00:00``。
    """
    step = interval_seconds(timeframe)
    epoch = int(moment.timestamp())
    aligned = epoch - (epoch % step)
    return dt.datetime.fromtimestamp(aligned, tz=dt.timezone.utc)


def is_candle_closed(candle: Candle, *, at: dt.datetime | None = None) -> bool:
    """判断 K 线是否已收盘（29.5.1）。

    以 ``close_time <= now`` 为准。当前时间必须可注入（36.1.3）。
    """
    moment = at or clock_now()
    return candle.close_time <= moment


def validate_ohlc(candle: Candle) -> str | None:
    """校验 OHLC 合法性。返回错误描述；合法时返回 ``None``。

    规则（4.3）：

    - ``high >= max(open, close, low)``
    - ``high >= low``
    - ``volume >= 0``、``quote_volume >= 0``
    """
    if candle.high < candle.low:
        return f"high({candle.high}) < low({candle.low})"
    if candle.high < max(candle.open, candle.close):
        return (
            f"high({candle.high}) < max(open({candle.open}), close({candle.close}))"
        )
    if candle.volume < 0:
        return f"volume({candle.volume}) < 0"
    if candle.quote_volume < 0:
        return f"quote_volume({candle.quote_volume}) < 0"
    if candle.open_time > candle.close_time:
        return f"open_time({candle.open_time}) > close_time({candle.close_time})"
    return None


def validate_candle(candle: Candle, *, at: dt.datetime | None = None) -> list[QualityEvent]:
    """对单根 K 线做完整校验，返回触发的质量事件列表。

    **未收盘 K 线直接拒绝**（不是质量问题，而是正常流程：等待收盘）。
    """
    events: list[QualityEvent] = []

    if not is_candle_closed(candle, at=at):
        # 未收盘：静默丢弃，不产生质量事件（否则每秒都会刷一条 WARN）
        return events

    error = validate_ohlc(candle)
    if error is not None:
        events.append(
            QualityEvent(
                rule_id="DATA_OHLC_INVALID",
                severity=Severity.CRITICAL,
                data_quality=DataQuality.INVALID,
                detected_by="SYSTEM",
                context={"reason": error, "candle": _candle_context(candle)},
                symbol=candle.symbol,
                market_type=candle.market_type,
                timeframe=candle.timeframe,
            )
        )
    return events


def find_gaps(
    candles: list[Candle],
    *,
    symbol: str,
    market_type: str,
    timeframe: str,
) -> list[dict[str, object]]:
    """检测 K 线缺口（29.4.1）。

    输入必须按 ``open_time`` 升序。返回缺口描述列表，每项含
    ``expected_open_time`` / ``missing_bars`` / ``prev_close_time``。
    """
    step = interval_seconds(timeframe)
    gaps: list[dict[str, object]] = []
    ordered = sorted(candles, key=lambda c: c.open_time)
    for previous, current in zip(ordered, ordered[1:], strict=False):
        delta = (current.open_time - previous.open_time).total_seconds()
        if delta <= step:
            continue
        missing = int(delta // step) - 1
        if missing <= 0:
            continue
        gaps.append(
            {
                "symbol": symbol,
                "market_type": market_type,
                "timeframe": timeframe,
                "expected_open_time": (
                    previous.open_time + dt.timedelta(seconds=step)
                ).isoformat(),
                "prev_close_time": previous.close_time.isoformat(),
                "missing_bars": missing,
            }
        )
    return gaps


def gap_severity(missing_bars: int, *, warn_bars: int = 1, critical_bars: int = 3) -> Severity:
    """按缺失根数定级（22.2.5 DATA_GAP）。"""
    if missing_bars >= critical_bars:
        return Severity.CRITICAL
    if missing_bars >= warn_bars:
        return Severity.WARN
    return Severity.INFO


def staleness_seconds(last_update: dt.datetime, *, at: dt.datetime | None = None) -> float:
    """数据陈旧秒数。"""
    moment = at or clock_now()
    return max(0.0, (moment - last_update).total_seconds())


def evaluate_staleness(
    last_update: dt.datetime,
    *,
    warn_seconds: int = 180,
    critical_seconds: int = 600,
    at: dt.datetime | None = None,
) -> QualityEvent | None:
    """评估数据陈旧程度（22.2.5 DATA_STALE）。"""
    age = staleness_seconds(last_update, at=at)
    if age >= critical_seconds:
        severity = Severity.CRITICAL
    elif age >= warn_seconds:
        severity = Severity.WARN
    else:
        return None
    return QualityEvent(
        rule_id="DATA_STALE",
        severity=severity,
        data_quality=DataQuality.STALE,
        detected_by="SCHEDULER",
        context={"staleness_seconds": age, "last_update": last_update.isoformat()},
    )


def is_taker_buy(is_buyer_maker: bool) -> bool:
    """主动买卖方向转换（30.2）。

    Binance 定义 ``isBuyerMaker=true`` 表示买方是挂单方，即该笔为**主动卖出**。
    本项目统一存 ``is_taker_buy = NOT is_buyer_maker``。
    """
    return not is_buyer_maker


def _candle_context(candle: Candle) -> dict[str, object]:
    return {
        "symbol": candle.symbol,
        "market_type": candle.market_type,
        "timeframe": candle.timeframe,
        "open_time": candle.open_time.isoformat(),
        "close_time": candle.close_time.isoformat(),
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "volume": str(candle.volume),
    }
