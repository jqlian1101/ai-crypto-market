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
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.orm import Session
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from aicrypto.common.clock import now as clock_now
from aicrypto.config.settings import Settings
from aicrypto.infrastructure import metrics
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
from aicrypto.infrastructure.proxy import websockets_proxy
from aicrypto.infrastructure.rate_limiter import RateLimitedError
from aicrypto.services.market_ingest import (
    IngestAction,
    PersistOutcome,
    cache_unclosed,
    persist_closed_candle,
    route_event,
)
from aicrypto.services.market_monitor import (
    INCREMENTAL_GAP_BARS,
    LATENCY_ALERT_MS,
    REST_FAILURE_THRESHOLD,
    build_clock_skew_event,
    build_gap_event,
    build_latency_event,
    build_rest_unavailable_event,
    clock_offset_ms,
    find_open_time_gaps,
    is_clock_skewed,
    latency_ms,
    to_ms,
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
        self._rest_failures = 0
        self._clock_offset: int | None = None
        #: WS 是否已完成恢复并在消费；交叉核对只在此期间运行
        self._live = False
        #: 恢复与交叉核对互斥，避免同一 (symbol, tf) 并发对账
        self._reconcile_lock = asyncio.Lock()

    # ------------------------------------------------------------------ 生命周期

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """主循环：直到 ``stop()`` 被调用才退出。

        同时启动两个后台任务：时钟校准（29.8）与通路 B 交叉核对（29.4.2）。
        """
        background = [
            asyncio.create_task(
                self._periodic(
                    self._sync_clock,
                    self._settings.CLOCK_SYNC_INTERVAL_SECONDS,
                    immediately=True,
                )
            ),
            asyncio.create_task(
                self._periodic(
                    self._crosscheck,
                    self._settings.MARKET_CROSSCHECK_SECONDS,
                    immediately=False,
                )
            ),
        ]
        try:
            await self._ws_loop()
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)

    async def _periodic(
        self, job: Callable[[], Awaitable[None]], interval: float, *, immediately: bool
    ) -> None:
        """按固定间隔执行 ``job``；单次异常只记日志，不终止循环。"""
        if not immediately and await self._sleep_or_stop(interval):
            return
        while not self._stop.is_set():
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "后台任务异常",
                    market_type=self._market_type,
                    job=getattr(job, "__name__", "job"),
                    error=str(exc),
                )
            if await self._sleep_or_stop(interval):
                return

    async def _sleep_or_stop(self, seconds: float) -> bool:
        """等待 ``seconds``；期间收到停止信号返回 True。"""
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        return self._stop.is_set()

    async def _ws_loop(self) -> None:
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
            proxy=websockets_proxy(self._settings.MARKET_PROXY),
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
            metrics.market_ws_connections_total.labels(market_type=self._market_type).inc()
            if self._disconnected_at is not None:
                metrics.market_ws_reconnects_total.labels(market_type=self._market_type).inc()

            # 1-2. 先恢复：REST 对账 + 缺口补齐 + 断线事件
            await self._recover(state)
            # 3. 恢复完成后才开始消费
            self._attempt = 0
            self._disconnected_at = None
            self._live = True
            metrics.market_ws_connected.labels(market_type=self._market_type).set(1)
            try:
                await self._consume(ws, state)
            finally:
                self._live = False
                metrics.market_ws_connected.labels(market_type=self._market_type).set(0)

    # ------------------------------------------------------------------ 恢复（29.3.2）

    async def _recover(self, state: _Session) -> None:
        disconnected_at = self._disconnected_at
        async with self._reconcile_lock:
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
        await self._reconcile_window(
            symbol, tf, start_ms=start_ms, end_ms=end_ms, detected_by="REST"
        )

    async def _reconcile_window(
        self, symbol: str, tf: str, *, start_ms: int, end_ms: int, detected_by: str
    ) -> None:
        """REST 拉 ``[start_ms, end_ms)`` 并与本地对账。REST 失败计入连续失败次数（29.9）。"""
        fetched_at = self._clock()
        try:
            rows = await self._rest.fetch_closed_klines(
                market_type=self._market_type,
                symbol=symbol,
                timeframe=tf,
                start_ms=start_ms,
                end_ms=end_ms,
                fetched_at=fetched_at,
            )
        except (httpx.HTTPError, RateLimitedError) as exc:
            await self._on_rest_failure(exc)
            raise
        self._rest_failures = 0
        await asyncio.to_thread(
            self._apply_recovery, symbol, tf, start_ms, rows, fetched_at, detected_by
        )

    async def _on_rest_failure(self, exc: Exception) -> None:
        self._rest_failures += 1
        logger.warning(
            "REST 请求失败",
            market_type=self._market_type,
            consecutive_failures=self._rest_failures,
            error=str(exc),
        )
        # 恰好达到阈值时记一次，持续失败不刷屏；恢复成功后计数归零
        if self._rest_failures == REST_FAILURE_THRESHOLD:
            event = build_rest_unavailable_event(
                market_type=self._market_type,
                failures=self._rest_failures,
                error=f"{type(exc).__name__}: {exc}",
                detected_at=self._clock(),
            )
            await asyncio.to_thread(self._add_events, [event])

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
        detected_by: str,
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
                detected_by=detected_by,
                now=fetched_at,
            )
        if result.missing:
            metrics.market_gaps_total.labels(
                symbol=symbol, market_type=self._market_type, timeframe=tf
            ).inc(result.missing)
        if not plan.is_clean:
            logger.warning(
                "恢复对账发现差异",
                symbol=symbol,
                timeframe=tf,
                missing=result.missing,
                divergent=result.divergent,
                local_only=result.local_only,
            )

    def _add_events(self, events: list[Any]) -> None:
        with self._db() as db:
            db.add_all(events)
        for event in events:
            metrics.market_data_quality_events_total.labels(
                rule_id=event.rule_id, severity=event.severity
            ).inc()

    # ------------------------------------------------------------------ 后台：时钟（29.8）

    async def _sync_clock(self) -> None:
        sent_ms = to_ms(self._clock())
        try:
            server_ms = await self._rest.fetch_server_time(self._market_type)
        except (httpx.HTTPError, RateLimitedError) as exc:
            await self._on_rest_failure(exc)
            raise
        self._rest_failures = 0
        received_ms = to_ms(self._clock())
        offset = clock_offset_ms(server_ms, sent_ms=sent_ms, received_ms=received_ms)
        self._clock_offset = offset
        metrics.market_clock_offset_ms.set(offset)
        threshold = self._settings.CLOCK_SKEW_ALERT_MS
        if is_clock_skewed(offset, threshold_ms=threshold):
            logger.error("本地时钟偏移超阈", market_type=self._market_type, offset_ms=offset)
            event = build_clock_skew_event(
                market_type=self._market_type,
                offset_ms=offset,
                threshold_ms=threshold,
                detected_at=self._clock(),
            )
            await asyncio.to_thread(self._add_events, [event])
        else:
            logger.info("时钟校准完成", market_type=self._market_type, offset_ms=offset)

    # ------------------------------------------------------------------ 后台：交叉核对（29.4.2）

    async def _crosscheck(self) -> None:
        """仅在 WS 已恢复并消费时运行：REST 最近 N 根与本地对账。"""
        if not self._live:
            return
        for symbol in self._symbols:
            for tf in self._timeframes:
                if not self._live or self._stop.is_set():
                    return
                async with self._reconcile_lock:
                    end_ms = closed_end_ms(self._clock(), tf)
                    start_ms = end_ms - INCREMENTAL_GAP_BARS * TIMEFRAME_MS[tf]
                    try:
                        await self._reconcile_window(
                            symbol, tf, start_ms=start_ms, end_ms=end_ms, detected_by="CROSSCHECK"
                        )
                    except (httpx.HTTPError, RateLimitedError):
                        # 已在 _reconcile_window 中计数与告警；本轮跳过，下一轮再试
                        continue

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
        moment = self._clock()
        row = event.row
        with self._db() as db:
            outcome = persist_closed_candle(db, row, now=moment)
            if outcome is not PersistOutcome.INSERTED:
                return outcome.value
            quality_events = self._post_insert_checks(db, row, moment)
        metrics.market_candles_written_total.labels(
            symbol=row["symbol"],
            market_type=self._market_type,
            timeframe=row["timeframe"],
            source="WS",
        ).inc()
        for q in quality_events:
            metrics.market_data_quality_events_total.labels(
                rule_id=q.rule_id, severity=q.severity
            ).inc()
        return outcome.value

    def _post_insert_checks(
        self, db: Session, row: dict[str, Any], moment: dt.datetime
    ) -> list[Any]:
        """29.8 延迟 + 29.4.1 增量连续性。事件与 K 线同一事务写入。"""
        symbol, tf, open_time = row["symbol"], row["timeframe"], row["open_time"]
        events: list[Any] = []

        latency = latency_ms(moment, row["exchange_ts"])
        metrics.market_data_latency_ms.labels(
            symbol=symbol, market_type=self._market_type, timeframe=tf, source="WS"
        ).observe(latency)
        if latency > LATENCY_ALERT_MS:
            events.append(
                build_latency_event(
                    market_type=self._market_type,
                    symbol=symbol,
                    timeframe=tf,
                    open_time=open_time,
                    latency=latency,
                    detected_at=moment,
                )
            )

        # 只检查新写入那根与其前一根之间：更早的缺口已在当时报告，避免重复事件
        step = TIMEFRAME_MS[tf]
        since = open_time - dt.timedelta(milliseconds=INCREMENTAL_GAP_BARS * step)
        prior = [
            b.open_time
            for b in load_local_bars(
                db, market_type=self._market_type, symbol=symbol, timeframe=tf, since=since
            )
            if b.open_time < open_time
        ]
        gaps: list[dict[str, Any]] = []
        if prior:
            prev = max(prior)
            if to_ms(open_time) - to_ms(prev) > step:
                gaps = find_open_time_gaps([prev, open_time], tf)
        if gaps:
            metrics.market_gaps_total.labels(
                symbol=symbol, market_type=self._market_type, timeframe=tf
            ).inc(sum(int(g["missing_bars"]) for g in gaps))
            logger.error("WS 写入后发现缺口", symbol=symbol, timeframe=tf, gaps=gaps)
            events.append(
                build_gap_event(
                    market_type=self._market_type,
                    symbol=symbol,
                    timeframe=tf,
                    gaps=gaps,
                    detected_at=moment,
                )
            )
        db.add_all(events)
        return events


__all__ = ["MarketCollector", "should_rotate"]
