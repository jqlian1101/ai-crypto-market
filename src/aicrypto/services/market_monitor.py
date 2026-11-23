"""采集层运行时监控的纯函数（PRD 29.4.1 / 29.8 / 29.9）。

- 时钟偏移：``clock_offset_ms = server_time - 本地时间``，取请求往返中点；
- 延迟：``latency_ms = processed_at - exchange_ts``，超过 2000ms 记 DEGRADED；
- 增量连续性：写入后检查最后 N 根，出现缺口产生 ``DATA_GAP``；
- REST 连续失败：产生 ``DATA_STALE``（CRITICAL）。

本模块只构造事件，不做 IO；写库与指标由 :mod:`market_collector` 负责。
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Sequence
from typing import Any

from aicrypto.common.ids import new_id
from aicrypto.domain.market.quality import DataQuality, Severity
from aicrypto.infrastructure.binance_rest import TIMEFRAME_MS
from aicrypto.infrastructure.database.models.market import MarketDataQualityEvent

CLOCK_SKEW_RULE_ID = "SYS_CLOCK_SKEW"
LATENCY_RULE_ID = "DATA_LATENCY_HIGH"
STALE_RULE_ID = "DATA_STALE"
GAP_RULE_ID = "DATA_GAP"

#: 29.8：单样本延迟超阈
LATENCY_ALERT_MS = 2000
#: 增量连续性检查回看根数（29.4.1「只检查最后 N 根」）
INCREMENTAL_GAP_BARS = 20
#: REST 连续失败多少次判定不可用（29.9）
REST_FAILURE_THRESHOLD = 3


def to_ms(moment: dt.datetime) -> int:
    return int(moment.timestamp() * 1000)


def clock_offset_ms(server_ms: int, *, sent_ms: int, received_ms: int) -> int:
    """以请求往返中点作为服务器时间对应的本地时刻，消除单程网络延迟。"""
    return server_ms - (sent_ms + received_ms) // 2


def is_clock_skewed(offset_ms: int, *, threshold_ms: int) -> bool:
    return abs(offset_ms) > threshold_ms


def latency_ms(processed_at: dt.datetime, exchange_ts: dt.datetime) -> int:
    return to_ms(processed_at) - to_ms(exchange_ts)


def find_open_time_gaps(open_times: Sequence[dt.datetime], timeframe: str) -> list[dict[str, Any]]:
    """相邻 open_time 差值大于周期即为缺口；缺失根数 = delta / interval - 1。"""
    step = TIMEFRAME_MS[timeframe]
    gaps: list[dict[str, Any]] = []
    for prev, cur in itertools.pairwise(sorted(open_times)):
        delta = to_ms(cur) - to_ms(prev)
        if delta <= step:
            continue
        gaps.append(
            {
                "after_open_time": prev.isoformat(),
                "before_open_time": cur.isoformat(),
                "missing_bars": delta // step - 1,
            }
        )
    return gaps


def _event(
    *,
    rule_id: str,
    severity: Severity,
    quality: DataQuality,
    detected_by: str,
    detected_at: dt.datetime,
    market_type: str | None,
    context: dict[str, Any],
    symbol: str | None = None,
    timeframe: str | None = None,
) -> MarketDataQualityEvent:
    return MarketDataQualityEvent(
        id=new_id("dqe", now=detected_at),
        detected_at=detected_at,
        exchange="binance",
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        rule_id=rule_id,
        severity=severity.value,
        data_quality=quality.value,
        detected_by=detected_by,
        context=context,
    )


def build_clock_skew_event(
    *, market_type: str, offset_ms: int, threshold_ms: int, detected_at: dt.datetime
) -> MarketDataQualityEvent:
    """29.8：偏移 > 阈值时的系统告警，质量标记 STALE。"""
    return _event(
        rule_id=CLOCK_SKEW_RULE_ID,
        severity=Severity.CRITICAL,
        quality=DataQuality.STALE,
        detected_by="SYSTEM",
        detected_at=detected_at,
        market_type=market_type,
        context={"clock_offset_ms": offset_ms, "threshold_ms": threshold_ms},
    )


def build_latency_event(
    *,
    market_type: str,
    symbol: str,
    timeframe: str,
    open_time: dt.datetime,
    latency: int,
    detected_at: dt.datetime,
) -> MarketDataQualityEvent:
    return _event(
        rule_id=LATENCY_RULE_ID,
        severity=Severity.WARN,
        quality=DataQuality.DEGRADED,
        detected_by="WS",
        detected_at=detected_at,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        context={
            "open_time": open_time.isoformat(),
            "latency_ms": latency,
            "threshold_ms": LATENCY_ALERT_MS,
        },
    )


def build_gap_event(
    *,
    market_type: str,
    symbol: str,
    timeframe: str,
    gaps: list[dict[str, Any]],
    detected_at: dt.datetime,
) -> MarketDataQualityEvent:
    """29.9：单周期数据缺失 -> ``DATA_GAP``（CRITICAL）。"""
    return _event(
        rule_id=GAP_RULE_ID,
        severity=Severity.CRITICAL,
        quality=DataQuality.MISSING,
        detected_by="SYSTEM",
        detected_at=detected_at,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        context={
            "gaps": gaps,
            "missing_bars": sum(int(g["missing_bars"]) for g in gaps),
            "check": "INCREMENTAL",
        },
    )


def build_rest_unavailable_event(
    *, market_type: str, failures: int, error: str, detected_at: dt.datetime
) -> MarketDataQualityEvent:
    return _event(
        rule_id=STALE_RULE_ID,
        severity=Severity.CRITICAL,
        quality=DataQuality.STALE,
        detected_by="REST",
        detected_at=detected_at,
        market_type=market_type,
        context={"reason": "REST_UNAVAILABLE", "consecutive_failures": failures, "error": error},
    )


__all__ = [
    "CLOCK_SKEW_RULE_ID",
    "INCREMENTAL_GAP_BARS",
    "LATENCY_ALERT_MS",
    "LATENCY_RULE_ID",
    "REST_FAILURE_THRESHOLD",
    "STALE_RULE_ID",
    "build_clock_skew_event",
    "build_gap_event",
    "build_latency_event",
    "build_rest_unavailable_event",
    "clock_offset_ms",
    "find_open_time_gaps",
    "is_clock_skewed",
    "latency_ms",
    "to_ms",
]
