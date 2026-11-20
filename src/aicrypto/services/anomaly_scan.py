"""异动扫描编排：读 K 线 → 规则引擎 → 身份/去重判定 → 写 ``anomaly_events``。

约束（PRD 22.x / 29.5）：

- 只触发 ``USDM_FUTURES``；现货不直接触发异动（第 22 章触发源说明）；
- 只读取已收盘 K 线，且 ``close_time``、``received_at``、``processed_at`` 均不晚于 cutoff；
- 去重与冷却判定由调用方注入已投递集合与最近触发时间，本模块不直接访问 Redis；
- 写入依赖 ``(fingerprint, revision)`` 唯一约束做最终兜底，冲突视为已存在而非错误。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aicrypto.domain.anomaly.dedup import decide_delivery
from aicrypto.domain.anomaly.engine import BarWindow, EngineResult, run_engine
from aicrypto.domain.anomaly.identity import (
    build_cooldown_key,
    build_event_id,
    build_fingerprint,
)
from aicrypto.infrastructure.database.models.anomaly import AnomalyEvent, AnomalyRule
from aicrypto.infrastructure.database.models.market import MarketCandle

SCAN_MARKET_TYPE = "USDM_FUTURES"
SCAN_SYMBOL_TIMEFRAMES = ("5m", "15m", "1h")
# 每个周期读取的根数：覆盖 ATR 预热（14）+ 基线（96）+ 当前根，并留余量。
BARS_PER_TIMEFRAME = 160


@dataclass(frozen=True, slots=True)
class NewEvent:
    """本轮新落库的事件；去重/冷却状态由调用方在落库成功后写入 Redis。"""

    fingerprint: str
    rule_id: str
    cooldown_key: str
    severity: str
    fired_at: dt.datetime


@dataclass(frozen=True, slots=True)
class ScanOutcome:
    symbol: str
    detected: int = 0
    persisted: int = 0
    suppressed: dict[str, int] = field(default_factory=dict)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    new_events: list[NewEvent] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LoadedBars:
    windows: dict[str, BarWindow]
    latest_open_time: dict[str, dt.datetime]


def load_bar_windows(
    db: Session,
    *,
    symbol: str,
    cutoff: dt.datetime,
    timeframes: tuple[str, ...] = SCAN_SYMBOL_TIMEFRAMES,
) -> LoadedBars:
    """读取各周期已收盘 K 线，按时间升序组装为 ``BarWindow``。

    cutoff 之后的数据一律不读，避免「未来函数」（29.5）。
    """
    windows: dict[str, BarWindow] = {}
    latest_open_time: dict[str, dt.datetime] = {}
    for frame in timeframes:
        rows = list(
            db.scalars(
                select(MarketCandle)
                .where(
                    MarketCandle.symbol == symbol,
                    MarketCandle.market_type == SCAN_MARKET_TYPE,
                    MarketCandle.exchange == "binance",
                    MarketCandle.timeframe == frame,
                    MarketCandle.is_closed.is_(True),
                    MarketCandle.close_time <= cutoff,
                    MarketCandle.received_at <= cutoff,
                    MarketCandle.processed_at <= cutoff,
                )
                .order_by(MarketCandle.close_time.desc())
                .limit(BARS_PER_TIMEFRAME)
            )
        )
        ordered = list(reversed(rows))
        if ordered:
            latest_open_time[frame] = ordered[-1].open_time
        windows[frame] = BarWindow(
            timeframe=frame,
            opens=[r.open for r in ordered],
            highs=[r.high for r in ordered],
            lows=[r.low for r in ordered],
            closes=[r.close for r in ordered],
            volumes=[r.volume for r in ordered],
        )
    return LoadedBars(windows=windows, latest_open_time=latest_open_time)


def _window_start(
    latest_open_time: Mapping[str, dt.datetime], rule_timeframe: str, cutoff: dt.datetime
) -> dt.datetime:
    """事件窗口起点：该周期最新已收盘根的 open_time；无数据时退回 cutoff。"""
    return latest_open_time.get(rule_timeframe, cutoff)


def scan_symbol(
    db: Session,
    *,
    symbol: str,
    cutoff: dt.datetime,
    disabled_rules: list[str],
    delivered_keys: set[str],
    last_fired_at: dict[str, dt.datetime],
) -> ScanOutcome:
    """对单个合约标的执行一轮扫描。

    ``delivered_keys`` / ``last_fired_at`` 由调用方从 Redis 注入；本函数只做纯判定与写库。
    """
    ensure_rule_version(db, now=cutoff)
    loaded = load_bar_windows(db, symbol=symbol, cutoff=cutoff)
    engine: EngineResult = run_engine(bars=loaded.windows, disabled_rules=disabled_rules)

    suppressed: dict[str, int] = {}
    new_events: list[NewEvent] = []
    persisted = 0
    errors = [f"{e.rule_id}: {e.message}" for e in engine.errors]

    for hit in engine.hits:
        base_rule = hit.rule_id.removesuffix("_CRIT")
        window_start = _window_start(
            loaded.latest_open_time, _timeframe_for_rule(base_rule), cutoff
        )
        fingerprint = build_fingerprint(
            symbol=symbol,
            market_type=SCAN_MARKET_TYPE,
            rule_id=hit.rule_id,
            window_start=window_start,
        )
        cooldown_key = build_cooldown_key(symbol=symbol, rule_id=hit.rule_id)
        decision = decide_delivery(
            fingerprint=fingerprint,
            revision=0,
            severity=hit.severity,
            cooldown_key=cooldown_key,
            now=cutoff,
            delivered_keys=delivered_keys,
            last_fired_at=last_fired_at,
        )
        if not decision.allowed:
            suppressed[decision.reason] = suppressed.get(decision.reason, 0) + 1
            continue

        values: dict[str, Any] = {
            "anomaly_event_id": build_event_id(fingerprint, 0),
            "detected_at": cutoff,
            "symbol": symbol,
            "market_type": SCAN_MARKET_TYPE,
            "rule_id": hit.rule_id,
            "rule_version": RULE_VERSION,
            "severity": hit.severity,
            "detected_by": "RULE_ENGINE",
            "window_start": window_start,
            "window_end": cutoff,
            "market_snapshot_id": None,
            "metrics": dict(hit.metrics),
            "threshold": dict(hit.threshold),
            "direction": hit.direction,
            "cooldown_key": cooldown_key,
            "fingerprint": fingerprint,
            "revision": 0,
            "revision_reason": "INITIAL_DETERMINISTIC",
            "notification_status": "PENDING",
        }
        # 用 RETURNING 判断是否真正新增：冲突跳过时不返回行。
        # 不依赖 rowcount，因为它在 ON CONFLICT DO NOTHING 下的语义不稳定。
        stmt = (
            insert(AnomalyEvent)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_anomaly_events_fp_rev")
            .returning(AnomalyEvent.anomaly_event_id)
        )
        inserted = db.execute(stmt).scalar_one_or_none()
        if inserted is not None:
            persisted += 1
            new_events.append(
                NewEvent(
                    fingerprint=fingerprint,
                    rule_id=hit.rule_id,
                    cooldown_key=cooldown_key,
                    severity=hit.severity,
                    fired_at=cutoff,
                )
            )

    return ScanOutcome(
        symbol=symbol,
        detected=len(engine.hits),
        persisted=persisted,
        suppressed=suppressed,
        skipped=len(engine.skipped),
        errors=errors,
        new_events=new_events,
    )


RULE_VERSION = "v1"

# v1 初始规则定义（PRD 22.2 初始阈值）。修改阈值须新增版本，不改已有行（30.6）。
# 引擎输出的每个 rule_id（含 _CRIT 变体）都必须有对应规则版本行，否则外键违例。
_V1_RULES: tuple[dict[str, Any], ...] = (
    {"rule_id": "PRICE_PCT_5M", "category": "PRICE", "severity": "WARN", "push_mode": "PUSH"},
    {
        "rule_id": "PRICE_PCT_5M_CRIT",
        "category": "PRICE",
        "severity": "CRITICAL",
        "push_mode": "PUSH",
    },
    {"rule_id": "PRICE_PCT_15M", "category": "PRICE", "severity": "WARN", "push_mode": "PUSH"},
    {
        "rule_id": "PRICE_PCT_15M_CRIT",
        "category": "PRICE",
        "severity": "CRITICAL",
        "push_mode": "PUSH",
    },
    {"rule_id": "PRICE_PCT_1H", "category": "PRICE", "severity": "WARN", "push_mode": "PUSH"},
    {
        "rule_id": "PRICE_PCT_1H_CRIT",
        "category": "PRICE",
        "severity": "CRITICAL",
        "push_mode": "PUSH",
    },
    {"rule_id": "VOL_ATR_SPIKE", "category": "VOLATILITY", "severity": "WARN", "push_mode": "PUSH"},
    {
        "rule_id": "VOL_ATR_SPIKE_CRIT",
        "category": "VOLATILITY",
        "severity": "CRITICAL",
        "push_mode": "PUSH",
    },
    {"rule_id": "VOL_REL_VOLUME", "category": "VOLUME", "severity": "WARN", "push_mode": "PUSH"},
    {
        "rule_id": "VOL_REL_VOLUME_CRIT",
        "category": "VOLUME",
        "severity": "CRITICAL",
        "push_mode": "PUSH",
    },
)


SEED_RULE_IDS = tuple(r["rule_id"] for r in _V1_RULES)


def ensure_rule_version(db: Session, *, now: dt.datetime) -> None:
    """幂等写入 v1 规则定义，保证 ``anomaly_events`` 的复合外键可满足。"""
    for rule in _V1_RULES:
        stmt = (
            insert(AnomalyRule)
            .values(
                rule_id=rule["rule_id"],
                rule_version=RULE_VERSION,
                category=rule["category"],
                description=f"{rule['rule_id']} 初始规则（PRD 22.2）",
                severity=rule["severity"],
                push_mode=rule["push_mode"],
                params={},
                asset_overrides={},
                market_type=SCAN_MARKET_TYPE,
                enabled=True,
                effective_from=now,
                effective_to=None,
                created_at=now,
                change_note="初始版本",
            )
            .on_conflict_do_nothing(constraint="pk_anomaly_rules")
        )
        db.execute(stmt)


def _timeframe_for_rule(base_rule_id: str) -> str:
    if base_rule_id.startswith("PRICE_PCT_5M"):
        return "5m"
    if base_rule_id.startswith("PRICE_PCT_15M"):
        return "15m"
    if base_rule_id.startswith("PRICE_PCT_1H"):
        return "1h"
    # 波动与成交量规则基于 1h。
    return "1h"


__all__ = [
    "BARS_PER_TIMEFRAME",
    "SCAN_MARKET_TYPE",
    "SEED_RULE_IDS",
    "ScanOutcome",
    "load_bar_windows",
    "scan_symbol",
]
