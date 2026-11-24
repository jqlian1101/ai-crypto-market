"""行情 WS 事件入库编排（PRD 29.4.2 / 29.5 / 29.7 / 29.8）。

约束：

- 只有 ``is_closed=True`` 且 ``close_time <= now`` 的 K 线才写入 ``market_candles``；
- 未收盘 K 线只进 Redis 临时键，TTL = 2 × 周期，绝不落库（29.5.1）；
- 已收盘 K 线按主键 upsert。WS 与 REST 价格冲突时以 REST 为准，并产生
  ``DATA_SOURCE_DIVERGENCE`` 质量事件（29.4.2）；
- 纯判定函数不做 IO，数据库与 Redis 写入集中在下方少数函数中。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aicrypto.common.clock import now as clock_now
from aicrypto.common.ids import new_id
from aicrypto.domain.market.quality import DataQuality, Severity, interval_seconds
from aicrypto.infrastructure.binance_ws import KlineEvent
from aicrypto.infrastructure.cache import build_cache_key, cache_set
from aicrypto.infrastructure.database.models.market import (
    MarketCandle,
    MarketDataQualityEvent,
)

DIVERGENCE_RULE_ID = "DATA_SOURCE_DIVERGENCE"
REST_SOURCES = frozenset({"REST", "REST_BACKFILL"})
UNCLOSED_CACHE_NAMESPACE = "market:unclosed"


class IngestAction(StrEnum):
    PERSIST = "PERSIST"
    CACHE_UNCLOSED = "CACHE_UNCLOSED"


class PersistOutcome(StrEnum):
    INSERTED = "INSERTED"
    UPDATED = "UPDATED"
    KEPT = "KEPT"
    UNCHANGED = "UNCHANGED"
    SKIPPED_NOT_CLOSED = "SKIPPED_NOT_CLOSED"


@dataclass(frozen=True, slots=True)
class Resolution:
    """已收盘 K 线冲突时的处理结论。"""

    overwrite: bool
    divergent: bool


def route_event(event: KlineEvent) -> IngestAction:
    """按 ``k.x`` 决定去向：已收盘落库，未收盘进缓存。"""
    return IngestAction.PERSIST if event.is_closed else IngestAction.CACHE_UNCLOSED


def unclosed_cache_key(market_type: str, symbol: str, timeframe: str) -> str:
    return build_cache_key(UNCLOSED_CACHE_NAMESPACE, market_type, symbol.upper(), timeframe)


def unclosed_ttl_seconds(timeframe: str) -> int:
    """未收盘临时键 TTL：2 × 周期（29.5.1）。"""
    return 2 * interval_seconds(timeframe)


def resolve_closed_conflict(
    *,
    existing_source: str,
    existing_close: Decimal,
    incoming_source: str,
    incoming_close: Decimal,
) -> Resolution:
    """已存在同主键已收盘 K 线时的判定。

    - 收盘价一致：保留现有行，不算分歧；
    - 收盘价不一致：分歧。REST 永远权威——仅当来者为 REST 时覆盖，WS 不覆盖已有行；
      两侧均为 WS 时保留先到者（同一根 K 线不应出现两个 WS 版本）。
    """
    if existing_close == incoming_close:
        return Resolution(overwrite=False, divergent=False)
    return Resolution(overwrite=incoming_source in REST_SOURCES, divergent=True)


def build_divergence_event(
    *,
    market_type: str,
    symbol: str,
    timeframe: str,
    open_time: dt.datetime,
    kept_source: str,
    kept_close: Decimal,
    incoming_source: str,
    incoming_close: Decimal,
    detected_at: dt.datetime,
) -> MarketDataQualityEvent:
    """构造 ``DATA_SOURCE_DIVERGENCE`` 质量事件（WARN，DEGRADED）。"""
    return MarketDataQualityEvent(
        id=new_id("dqe", now=detected_at),
        detected_at=detected_at,
        exchange="binance",
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        rule_id=DIVERGENCE_RULE_ID,
        severity=Severity.WARN.value,
        data_quality=DataQuality.DEGRADED.value,
        detected_by="CROSSCHECK",
        context={
            "open_time": open_time.isoformat(),
            "kept_source": kept_source,
            "kept_close": str(kept_close),
            "incoming_source": incoming_source,
            "incoming_close": str(incoming_close),
            "authoritative": "REST",
        },
    )


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    raise TypeError(f"不可序列化: {type(value)!r}")


def _serialize(row: dict[str, Any]) -> str:
    return json.dumps(row, default=_json_default, sort_keys=True)


async def cache_unclosed(event: KlineEvent) -> str:
    """未收盘 K 线写入 Redis 临时键，返回所用键名。"""
    key = unclosed_cache_key(event.market_type, event.symbol, event.timeframe)
    await cache_set(
        key,
        _serialize(event.row),
        ttl_seconds=unclosed_ttl_seconds(event.timeframe),
    )
    return key


def _pk_filters(row: dict[str, Any]) -> list[Any]:
    return [
        MarketCandle.exchange == row["exchange"],
        MarketCandle.market_type == row["market_type"],
        MarketCandle.symbol == row["symbol"],
        MarketCandle.timeframe == row["timeframe"],
        MarketCandle.open_time == row["open_time"],
    ]


def persist_closed_candle(
    db: Session,
    row: dict[str, Any],
    *,
    now: dt.datetime | None = None,
) -> PersistOutcome:
    """把一根已收盘 K 线 upsert 进 ``market_candles``。调用方负责 commit。

    ``processed_at`` 取真实处理时刻（29.8）。未到收盘时刻或 ``is_closed`` 为假的行直接拒绝。
    """
    moment = now or clock_now()
    if not row["is_closed"] or row["close_time"] > moment:
        return PersistOutcome.SKIPPED_NOT_CLOSED

    values = {**row, "processed_at": moment}
    existing = db.scalars(select(MarketCandle).where(*_pk_filters(row))).first()

    if existing is None:
        stmt = (
            insert(MarketCandle)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["exchange", "market_type", "symbol", "timeframe", "open_time"]
            )
            .returning(MarketCandle.open_time)
        )
        # 不依赖 rowcount：ORM insert 会自动追加 RETURNING，psycopg 此时
        # 返回 -1（未知），真假判断会永远成立。冲突跳过时 RETURNING 无行。
        inserted = db.execute(stmt).scalar_one_or_none()
        if inserted is not None:
            return PersistOutcome.INSERTED
        # 并发插入竞争：重读后按冲突路径处理
        existing = db.scalars(select(MarketCandle).where(*_pk_filters(row))).first()
        if existing is None:  # pragma: no cover - 数据库层不变量
            raise RuntimeError("upsert 冲突但未读到既有行")

    resolution = resolve_closed_conflict(
        existing_source=existing.source,
        existing_close=existing.close,
        incoming_source=row["source"],
        incoming_close=row["close"],
    )
    if resolution.divergent:
        db.add(
            build_divergence_event(
                market_type=row["market_type"],
                symbol=row["symbol"],
                timeframe=row["timeframe"],
                open_time=row["open_time"],
                kept_source=existing.source,
                kept_close=existing.close,
                incoming_source=row["source"],
                incoming_close=row["close"],
                detected_at=moment,
            )
        )
    if not resolution.overwrite:
        return PersistOutcome.KEPT if resolution.divergent else PersistOutcome.UNCHANGED

    db.execute(
        update(MarketCandle)
        .where(*_pk_filters(row))
        .values(
            close_time=row["close_time"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
            quote_volume=row["quote_volume"],
            trades_count=row["trades_count"],
            taker_buy_base_volume=row["taker_buy_base_volume"],
            taker_buy_quote_volume=row["taker_buy_quote_volume"],
            exchange_ts=row["exchange_ts"],
            received_at=row["received_at"],
            processed_at=moment,
            is_closed=True,
            source=row["source"],
        )
        .execution_options(synchronize_session=False)
    )
    return PersistOutcome.UPDATED


__all__ = [
    "DIVERGENCE_RULE_ID",
    "IngestAction",
    "PersistOutcome",
    "Resolution",
    "build_divergence_event",
    "cache_unclosed",
    "persist_closed_candle",
    "resolve_closed_conflict",
    "route_event",
    "unclosed_cache_key",
    "unclosed_ttl_seconds",
]
