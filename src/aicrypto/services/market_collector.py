"""行情 WS 采集器（PRD 29.3 / 29.3.2 / 29.4.2）。

单个 ``market_type`` 一条采集循环：

1. 建立组合流连接（30 秒无消息判半开，24 小时前主动轮换）；
2. **先恢复后写入**：REST 对账补缺口、覆盖分歧，并记录 ``DATA_WS_DISCONNECT``；
3. 恢复完成后才消费 WS 消息：已收盘 -> upsert 落库，未收盘 -> Redis 临时键；
4. 任何异常都进入带抖动的指数退避重连，不因单次故障退出。

WS 消息在恢复期间留在 socket 缓冲区中，不会被处理。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from aicrypto.common.clock import now as clock_now
from aicrypto.config.settings import Settings
from aicrypto.infrastructure.binance_rest import TIMEFRAME_MS
from aicrypto.infrastructure.binance_rest_client import BinanceKlineClient, closed_end_ms
from aicrypto.infrastructure.binance_ws import (
    HALF_OPEN_TIMEOUT_SECONDS,
    MAX_CONNECTION_AGE_SECONDS,
    KlineEvent,
    build_stream_url,
    parse_kline_message,
    reconnect_delay_seconds,
    stream_names,
)
from aicrypto.infrastructure.database.session import session_scope
from aicrypto.infrastructure.logging import get_logger
from aicrypto.services.market_ingest import (
    IngestAction,
    cache_unclosed,
    persist_closed_candle,
    route_event,
)
from aicrypto.services.market_recovery import (
    apply_plan,
    build_disconnect_event,
    last_local_open_time,
    load_local_bars,
    reconcile,
    recovery_window_bars,
)

logger = get_logger(__name__)

Clock = Callable[[], dt.datetime]
#: 返回「退出时 commit、异常时 rollback」的会话作用域，默认 ``session_scope``
DbScope = Callable[[], AbstractContextManager[Session]]


def should_rotate(connected_at: float, *, monotonic_now: float) -> bool:
    """连接存活超过 ``MAX_CONNECTION_AGE_SECONDS`` 时需要主动轮换。"""
    return monotonic_now - connected_at >= MAX_CONNECTION_AGE_SECONDS


@dataclass(slots=True)
class _Session:
    """一次连接的运行时状态。"""

    connected_at: float
    last_message_at: float
    recovered: bool = False
    counters: dict[str, int] = field(default_factory=dict)

    def bump(self, key: str) -> None:
        self.counters[key] = self.counters.get(key, 0) + 1


class MarketCollector:
    def __init__(
        self,
        *,
        settings: Settings,
        market_type: str,
        symbols: Sequence[str],
        timeframes: Sequence[str],
        rest: BinanceKlineClient,
        db_scope: DbScope | None = None,
        clock: Clock = clock_now,
        monotonic: Callable[[], float] = time.monotonic,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self._settings = settings
        self._market_type = market_type
        self._symbols = [s.upper() for s in symbols]
        self._timeframes = list(timeframes)
        self._rest = rest
        self._db: DbScope = db_scope or (lambda: session_scope(settings))
        self._clock = clock
        self._monotonic = monotonic
        self._stop = stop_event or asyncio.Event()
        self._url = build_stream_url(market_type, stream_names(self._symbols, self._timeframes))
        self._attempt = 0
        self._disconnected_at: dt.datetime | None = None

    # ------------------------------------------------------------------ 生命周期

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """主循环：直到 ``stop()`` 被调用才退出。"""
        while not self._stop.is_set():
            try:
                await self._run_connection()
                reason = "rotated_or_closed"
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, TimeoutError, OSError) as exc:
                reason = type(exc).__name__
            except Exception as exc:  # 未知错误也不能让采集永久停止
                reason = f"error:{type(exc).__name__}"
                logger.error("采集循环异常", market_type=self._market_type, error=str(exc))

            if self._disconnected_at is None:
                self._disconnected_at = self._clock()
            delay = reconnect_delay_seconds(
                self._attempt, max_ms=self._settings.MARKET_WS_RECONNECT_MAX_MS
            )
            self._attempt += 1
            logger.warning(
                "WS 断开，准备重连",
                market_type=self._market_type,
                reason=reason,
                delay_seconds=round(delay, 3),
            )
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)

    async def _run_connection(self) -> None:
        async with connect(
            self._url,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            max_queue=4096,
        ) as ws:
            state = _Session(
                connected_at=self._monotonic(),
                last_message_at=self._monotonic(),
            )
            logger.info("WS 已连接", market_type=self._market_type, streams=len(self._symbols))

            # 1-2. 先恢复：REST 对账 + 缺口补齐 + 断线事件
            await self._recover(state)
            # 3. 恢复完成后才开始消费
            self._attempt = 0
            self._disconnected_at = None
            await self._consume(ws, state)

    # ------------------------------------------------------------------ 恢复（29.3.2）

    async def _recover(self, state: _Session) -> None:
        disconnected_at = self._disconnected_at
        for symbol in self._symbols:
            for tf in self._timeframes:
                await self._recover_pair(symbol, tf)
        if disconnected_at is not None:
            await asyncio.to_thread(self._write_disconnect_event, disconnected_at)
        state.recovered = True

    async def _recover_pair(self, symbol: str, tf: str) -> None:
        end_ms = closed_end_ms(self._clock(), tf)
        last_open = await asyncio.to_thread(self._last_open, symbol, tf)
        bars = recovery_window_bars(last_local_open=last_open, end_ms=end_ms, timeframe=tf)
        interval_ms = TIMEFRAME_MS[tf]
        start_ms = end_ms - bars * interval_ms
        fetched_at = self._clock()
        rows = await self._rest.fetch_closed_klines(
            market_type=self._market_type,
            symbol=symbol,
            timeframe=tf,
            start_ms=start_ms,
            end_ms=end_ms,
            fetched_at=fetched_at,
        )
        await asyncio.to_thread(self._apply_recovery, symbol, tf, start_ms, rows, fetched_at)

    def _last_open(self, symbol: str, tf: str) -> dt.datetime | None:
        with self._db() as db:
            return last_local_open_time(
                db, market_type=self._market_type, symbol=symbol, timeframe=tf
            )

    def _apply_recovery(
        self,
        symbol: str,
        tf: str,
        start_ms: int,
        rows: list[dict[str, Any]],
        fetched_at: dt.datetime,
    ) -> None:
        since = dt.datetime.fromtimestamp(start_ms / 1000, tz=dt.UTC)
        with self._db() as db:
            local = load_local_bars(
                db, market_type=self._market_type, symbol=symbol, timeframe=tf, since=since
            )
            plan = reconcile(local, rows)
            result = apply_plan(
                db,
                plan,
                market_type=self._market_type,
                symbol=symbol,
                timeframe=tf,
                fetched=len(rows),
                detected_by="REST",
                now=fetched_at,
            )
        if not plan.is_clean:
            logger.warning(
                "恢复对账发现差异",
                symbol=symbol,
                timeframe=tf,
                missing=result.missing,
                divergent=result.divergent,
                local_only=result.local_only,
            )

    def _write_disconnect_event(self, disconnected_at: dt.datetime) -> None:
        event = build_disconnect_event(
            market_type=self._market_type,
            disconnected_at=disconnected_at,
            reconnected_at=self._clock(),
            reason="reconnect",
        )
        with self._db() as db:
            db.add(event)

    # ------------------------------------------------------------------ 消费

    async def _consume(self, ws: ClientConnection, state: _Session) -> None:
        while not self._stop.is_set():
            if should_rotate(state.connected_at, monotonic_now=self._monotonic()):
                logger.info("连接达到存活上限，主动轮换", market_type=self._market_type)
                return
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=HALF_OPEN_TIMEOUT_SECONDS)
            except TimeoutError as exc:
                raise TimeoutError("30 秒无消息，判定半开") from exc
            state.last_message_at = self._monotonic()
            await self._handle_message(raw, state)

    async def _handle_message(self, raw: str | bytes, state: _Session) -> None:
        try:
            message = json.loads(raw)
            event = parse_kline_message(
                message, market_type=self._market_type, received_at=self._clock()
            )
        except (ValueError, TypeError) as exc:
            state.bump("parse_error")
            logger.warning("WS 消息解析失败", market_type=self._market_type, error=str(exc))
            return
        if event is None:
            return
        await self._route(event, state)

    async def _route(self, event: KlineEvent, state: _Session) -> None:
        if route_event(event) is IngestAction.CACHE_UNCLOSED:
            await cache_unclosed(event)
            state.bump("cached_unclosed")
            return
        outcome = await asyncio.to_thread(self._persist, event)
        state.bump(outcome)

    def _persist(self, event: KlineEvent) -> str:
        with self._db() as db:
            return persist_closed_candle(db, event.row, now=self._clock()).value


__all__ = ["MarketCollector", "should_rotate"]
