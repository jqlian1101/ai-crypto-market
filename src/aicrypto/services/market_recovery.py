"""REST 对账：重连恢复（29.3.2）与通路 B 交叉核对（29.4.2）共用。

流程：REST 拉最近 N 根已收盘 K 线 → 与本地比对 → 补缺口 / 以 REST 覆盖分歧 → 写质量事件。

判定（29.4.2）：

=====================================  ==============================================
差异                                    动作
=====================================  ==============================================
REST 有、本地无                          用 REST 补齐，``DATA_GAP``
close 差异 > 1 tick                     以 REST 覆盖，``DATA_SOURCE_DIVERGENCE``
close 差异 <= 1 tick                    不处理
本地有、REST 无                          只记 ``INVALID`` 质量事件，**不删除**（29.9 原则）
=====================================  ==============================================
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aicrypto.common.ids import new_id
from aicrypto.domain.market.quality import DataQuality, Severity, gap_severity
from aicrypto.infrastructure.binance_rest import TIMEFRAME_MS, is_valid_ohlc
from aicrypto.infrastructure.database.models.market import (
    MarketCandle,
    MarketDataQualityEvent,
)
from aicrypto.services.market_ingest import DIVERGENCE_RULE_ID

GAP_RULE_ID = "DATA_GAP"
DISCONNECT_RULE_ID = "DATA_WS_DISCONNECT"
#: 本地脏数据与 REST 非法 K 线共用（29.9：交易所返回脏 K 线 -> DATA_QUALITY_DEGRADED）
DEGRADED_RULE_ID = "DATA_QUALITY_DEGRADED"

#: 默认回看根数（29.3.2 的「interval 回看」）
DEFAULT_LOOKBACK_BARS = 20
#: 单次恢复最多拉取的根数；更长的断线交给 ``scripts/backfill_market.py``
MAX_RECOVERY_BARS = 5000
#: 缺口之外多拉的根数（29.3.2：缺口长度 + 5）
GAP_PADDING_BARS = 5

_UPDATE_COLUMNS = (
    "close_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "exchange_ts",
    "received_at",
    "processed_at",
    "is_closed",
    "source",
)


@dataclass(frozen=True, slots=True)
class LocalBar:
    open_time: dt.datetime
    close: Decimal
    source: str


@dataclass(slots=True)
class ReconcilePlan:
    """对账结论。``writes`` 是需要以 REST 覆盖写入的行（缺失 + 分歧）。"""

    missing: list[dict[str, Any]] = field(default_factory=list)
    divergent: list[tuple[LocalBar, dict[str, Any]]] = field(default_factory=list)
    local_only: list[LocalBar] = field(default_factory=list)
    invalid_remote: list[dict[str, Any]] = field(default_factory=list)

    @property
    def writes(self) -> list[dict[str, Any]]:
        return [*self.missing, *(row for _, row in self.divergent)]

    @property
    def is_clean(self) -> bool:
        return not (self.missing or self.divergent or self.local_only or self.invalid_remote)


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    market_type: str
    symbol: str
    timeframe: str
    fetched: int
    written: int
    missing: int
    divergent: int
    local_only: int
    events: int


# ---------------------------------------------------------------- 纯函数


def recovery_window_bars(
    *,
    last_local_open: dt.datetime | None,
    end_ms: int,
    timeframe: str,
    lookback: int = DEFAULT_LOOKBACK_BARS,
    max_bars: int = MAX_RECOVERY_BARS,
) -> int:
    """N = max(lookback, 缺口长度 + 5)，上限 ``max_bars``（29.3.2）。

    本地无数据时只拉 ``lookback`` 根；冷启动全量历史由补数脚本负责。
    """
    if last_local_open is None:
        return min(lookback, max_bars)
    interval = TIMEFRAME_MS[timeframe]
    last_ms = int(last_local_open.timestamp() * 1000)
    gap_bars = max(0, (end_ms - last_ms) // interval - 1)
    return min(max(lookback, gap_bars + GAP_PADDING_BARS), max_bars)


def reconcile(
    local: Iterable[LocalBar],
    remote: Sequence[dict[str, Any]],
    *,
    tick_size: Decimal | None = None,
) -> ReconcilePlan:
    """比对本地与 REST。只在 REST 覆盖的 open_time 区间内判断「本地有、REST 无」。

    ``tick_size`` 为 None 时按严格相等比较 close。
    """
    plan = ReconcilePlan()
    tolerance = tick_size or Decimal(0)
    valid_remote: dict[dt.datetime, dict[str, Any]] = {}
    for row in remote:
        if is_valid_ohlc(row):
            valid_remote[row["open_time"]] = row
        else:
            plan.invalid_remote.append(row)

    remote_times = {row["open_time"] for row in remote}
    local_map = {bar.open_time: bar for bar in local}

    for open_time, row in sorted(valid_remote.items()):
        bar = local_map.get(open_time)
        if bar is None:
            plan.missing.append(row)
        elif abs(bar.close - row["close"]) > tolerance:
            plan.divergent.append((bar, row))

    if remote_times:
        lo, hi = min(remote_times), max(remote_times)
        plan.local_only = [
            bar for t, bar in sorted(local_map.items()) if lo <= t <= hi and t not in remote_times
        ]
    return plan


def missing_runs(open_times: Sequence[dt.datetime], timeframe: str) -> list[tuple[str, str, int]]:
    """把缺失的 open_time 合并为连续区间 ``(start, end, bars)``，便于事件上下文阅读。"""
    if not open_times:
        return []
    step = dt.timedelta(milliseconds=TIMEFRAME_MS[timeframe])
    ordered = sorted(open_times)
    runs: list[tuple[str, str, int]] = []
    start = prev = ordered[0]
    count = 1
    for t in ordered[1:]:
        if t - prev == step:
            prev, count = t, count + 1
            continue
        runs.append((start.isoformat(), prev.isoformat(), count))
        start = prev = t
        count = 1
    runs.append((start.isoformat(), prev.isoformat(), count))
    return runs


def _event(
    common: dict[str, Any],
    *,
    rule_id: str,
    severity: Severity,
    quality: DataQuality,
    context: dict[str, Any],
) -> MarketDataQualityEvent:
    return MarketDataQualityEvent(
        id=new_id("dqe"),
        detected_at=common["detected_at"],
        exchange="binance",
        market_type=common["market_type"],
        symbol=common["symbol"],
        timeframe=common["timeframe"],
        rule_id=rule_id,
        severity=severity.value,
        data_quality=quality.value,
        detected_by=common["detected_by"],
        context=context,
    )


def build_plan_events(
    plan: ReconcilePlan,
    *,
    market_type: str,
    symbol: str,
    timeframe: str,
    detected_by: str,
    detected_at: dt.datetime,
) -> list[MarketDataQualityEvent]:
    """按对账结论生成质量事件：每类差异一条，避免逐根刷屏。"""
    events: list[MarketDataQualityEvent] = []
    common = {
        "detected_by": detected_by,
        "detected_at": detected_at,
        "market_type": market_type,
        "symbol": symbol,
        "timeframe": timeframe,
    }
    if plan.missing:
        bars = len(plan.missing)
        events.append(
            _event(
                common,
                rule_id=GAP_RULE_ID,
                severity=gap_severity(bars),
                quality=DataQuality.MISSING,
                context={
                    "missing_bars": bars,
                    "runs": missing_runs([r["open_time"] for r in plan.missing], timeframe),
                    "action": "BACKFILLED_FROM_REST",
                },
            )
        )
    if plan.divergent:
        events.append(
            _event(
                common,
                rule_id=DIVERGENCE_RULE_ID,
                severity=Severity.WARN,
                quality=DataQuality.DEGRADED,
                context={
                    "authoritative": "REST",
                    "action": "OVERWRITTEN_BY_REST",
                    "bars": [
                        {
                            "open_time": bar.open_time.isoformat(),
                            "local_source": bar.source,
                            "local_close": str(bar.close),
                            "rest_close": str(row["close"]),
                        }
                        for bar, row in plan.divergent
                    ],
                },
            )
        )
    if plan.local_only:
        events.append(
            _event(
                common,
                rule_id=DEGRADED_RULE_ID,
                severity=Severity.WARN,
                quality=DataQuality.INVALID,
                context={
                    "reason": "LOCAL_BAR_NOT_IN_REST",
                    "action": "FLAGGED_NOT_DELETED",
                    "open_times": [b.open_time.isoformat() for b in plan.local_only],
                },
            )
        )
    if plan.invalid_remote:
        events.append(
            _event(
                common,
                rule_id=DEGRADED_RULE_ID,
                severity=Severity.WARN,
                quality=DataQuality.INVALID,
                context={
                    "reason": "REST_OHLC_INVALID",
                    "action": "REJECTED",
                    "open_times": [r["open_time"].isoformat() for r in plan.invalid_remote],
                },
            )
        )
    return events


def build_disconnect_event(
    *,
    market_type: str,
    disconnected_at: dt.datetime,
    reconnected_at: dt.datetime,
    reason: str,
) -> MarketDataQualityEvent:
    """``DATA_WS_DISCONNECT``（WARN，22.2.5）。按连接粒度记录，不区分 symbol。"""
    common: dict[str, Any] = {
        "detected_by": "WS",
        "detected_at": reconnected_at,
        "market_type": market_type,
        "symbol": None,
        "timeframe": None,
    }
    return _event(
        common,
        rule_id=DISCONNECT_RULE_ID,
        severity=Severity.WARN,
        quality=DataQuality.DEGRADED,
        context={
            "disconnected_at": disconnected_at.isoformat(),
            "reconnected_at": reconnected_at.isoformat(),
            "downtime_seconds": (reconnected_at - disconnected_at).total_seconds(),
            "reason": reason,
        },
    )


# ---------------------------------------------------------------- 数据库读写（调用方 commit）


def _key_filters(market_type: str, symbol: str, timeframe: str) -> list[Any]:
    return [
        MarketCandle.exchange == "binance",
        MarketCandle.market_type == market_type,
        MarketCandle.symbol == symbol,
        MarketCandle.timeframe == timeframe,
    ]


def last_local_open_time(
    db: Session, *, market_type: str, symbol: str, timeframe: str
) -> dt.datetime | None:
    return db.scalars(
        select(MarketCandle.open_time)
        .where(*_key_filters(market_type, symbol, timeframe))
        .order_by(MarketCandle.open_time.desc())
        .limit(1)
    ).first()


def load_local_bars(
    db: Session,
    *,
    market_type: str,
    symbol: str,
    timeframe: str,
    since: dt.datetime,
) -> list[LocalBar]:
    rows = db.execute(
        select(MarketCandle.open_time, MarketCandle.close, MarketCandle.source).where(
            *_key_filters(market_type, symbol, timeframe),
            MarketCandle.open_time >= since,
        )
    ).all()
    return [LocalBar(open_time=r[0], close=r[1], source=r[2]) for r in rows]


def upsert_rest_rows(db: Session, rows: Sequence[dict[str, Any]], *, now: dt.datetime) -> int:
    """REST 行幂等写入；主键冲突时以 REST 覆盖（29.4.2：REST 为准）。"""
    if not rows:
        return 0
    values = [{**row, "processed_at": now} for row in rows]
    stmt = insert(MarketCandle).values(values)
    stmt = stmt.on_conflict_do_update(
        constraint="pk_market_candles",
        set_={col: stmt.excluded[col] for col in _UPDATE_COLUMNS},
    )
    db.execute(stmt)
    return len(values)


def apply_plan(
    db: Session,
    plan: ReconcilePlan,
    *,
    market_type: str,
    symbol: str,
    timeframe: str,
    fetched: int,
    detected_by: str,
    now: dt.datetime,
) -> RecoveryResult:
    """写入补数 / 覆盖行与质量事件。调用方负责 commit。"""
    written = upsert_rest_rows(db, plan.writes, now=now)
    events = build_plan_events(
        plan,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        detected_by=detected_by,
        detected_at=now,
    )
    db.add_all(events)
    return RecoveryResult(
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        fetched=fetched,
        written=written,
        missing=len(plan.missing),
        divergent=len(plan.divergent),
        local_only=len(plan.local_only),
        events=len(events),
    )


__all__ = [
    "DEFAULT_LOOKBACK_BARS",
    "DISCONNECT_RULE_ID",
    "GAP_RULE_ID",
    "MAX_RECOVERY_BARS",
    "LocalBar",
    "ReconcilePlan",
    "RecoveryResult",
    "apply_plan",
    "build_disconnect_event",
    "build_plan_events",
    "last_local_open_time",
    "load_local_bars",
    "missing_runs",
    "reconcile",
    "recovery_window_bars",
    "upsert_rest_rows",
]
