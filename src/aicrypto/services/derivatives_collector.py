"""衍生品与盘口采集编排（PRD 29.2 / 29.4.3 / 29.7）。

两条独立的采集线，共享一个 REST 限流器（27.2）：

1. **轮询线**（REST）——Open Interest、资金费率历史、盘口深度快照、合约规格；
   - OI 每 ``MARKET_OI_POLL_SECONDS`` 秒轮询一次（默认 60s，29.4.3）；
   - 盘口每 ``MARKET_BOOK_SNAPSHOT_INTERVAL_SECONDS`` 秒一次（默认 60s，27.3）；
   - Funding 历史每 ``MARKET_FUNDING_POLL_SECONDS`` 秒一次（默认 600s）。
2. **流线**（WS）——``aggTrade``（聚合成交）与 ``markPrice@1s``（标记价格 + 当前预估费率）。

**OI 只能轮询**（29.2）：Binance 没有 OI 的 WS 推送，因此 ``DER_OI_SURGE``
的检出延迟 = 轮询间隔 + 1h 窗口对齐延迟，高于价格异动。这一点在指标与
日志里都要体现，不能假装它是实时的。

任何单次失败都不终止循环：失败计数达阈值时产生 ``DATA_STALE`` 质量事件，
恢复后计数归零。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractContextManager, suppress
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from aicrypto.common.clock import now as clock_now
from aicrypto.config.settings import Settings
from aicrypto.infrastructure import metrics
from aicrypto.infrastructure.binance_derivatives import (
    parse_depth_snapshot,
    parse_funding_rate,
    parse_instrument_metadata_list,
    parse_open_interest,
    parse_open_interest_history,
    parse_premium_index,
)
from aicrypto.infrastructure.binance_derivatives_client import BinanceDerivativesClient
from aicrypto.infrastructure.binance_derivatives_ws import (
    agg_trade_stream_names,
    build_stream_url,
    mark_price_stream_names,
    parse_agg_trade_message,
    parse_mark_price_message,
)
from aicrypto.infrastructure.binance_ws import (
    HALF_OPEN_TIMEOUT_SECONDS,
    MAX_CONNECTION_AGE_SECONDS,
    reconnect_delay_seconds,
)
from aicrypto.infrastructure.database.models.market import InstrumentMetadata
from aicrypto.infrastructure.database.session import session_scope
from aicrypto.infrastructure.logging import get_logger
from aicrypto.infrastructure.rate_limiter import RateLimitedError
from aicrypto.services.derivatives_ingest import (
    IngestOutcome,
    StaleCheck,
    build_stale_event,
    evaluate_staleness,
    persist_funding_rate,
    persist_open_interest,
    persist_orderbook,
    persist_trade,
)

logger = get_logger(__name__)

Clock = Callable[[], dt.datetime]
DbScope = Callable[[], AbstractContextManager[Session]]

#: OI 陈旧阈值（29.4.3：轮询 60s，允许滞后 3 分钟）
OI_STALE_THRESHOLD_SECONDS = 180.0
#: Funding 当前值陈旧阈值（29.4.3：1s 推送，允许滞后 2 分钟）
FUNDING_STALE_THRESHOLD_SECONDS = 120.0
#: Funding 历史陈旧阈值（29.4.3：允许 1 个周期）
FUNDING_HISTORY_STALE_THRESHOLD_SECONDS = 8 * 3600.0
#: REST 连续失败多少次判定不可用（与 K 线采集一致）
REST_FAILURE_THRESHOLD = 3


class DerivativesCollector:
    """衍生品与盘口采集器（一个 market_type 一条）。"""

    def __init__(
        self,
        *,
        settings: Settings,
        market_type: str,
        symbols: Sequence[str],
        rest: BinanceDerivativesClient,
        db_scope: DbScope | None = None,
        clock: Clock = clock_now,
        monotonic: Callable[[], float] = time.monotonic,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self._settings = settings
        self._market_type = market_type
        self._symbols = [s.upper() for s in symbols]
        self._rest = rest
        self._db: DbScope = db_scope or (lambda: session_scope(settings))
        self._clock = clock
        self._monotonic = monotonic
        self._stop = stop_event or asyncio.Event()
        self._attempt = 0
        self._rest_failures = 0
        self._url = build_stream_url(
            market_type,
            agg_trade_stream_names(self._symbols) + mark_price_stream_names(self._symbols),
        )
        self._mark_prices: dict[str, Any] = {}

    # ------------------------------------------------------------------ 生命周期

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """主循环：轮询线与流线并行，直到 ``stop()`` 被调用。"""
        tasks = [
            asyncio.create_task(self._poll_loop()),
            asyncio.create_task(self._ws_loop()),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _sleep_or_stop(self, seconds: float) -> bool:
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        return self._stop.is_set()

    # ------------------------------------------------------------------ 轮询线

    async def _poll_loop(self) -> None:
        """轮询线：OI / Funding / 盘口 / 合约规格。

        每个数据源有自己的节奏，但共用一条循环，避免一堆定时器互相干扰。
        """
        oi_interval = self._settings.MARKET_OI_POLL_SECONDS
        book_interval = self._settings.MARKET_BOOK_SNAPSHOT_INTERVAL_SECONDS
        funding_interval = self._settings.MARKET_FUNDING_POLL_SECONDS

        next_oi = 0.0
        next_book = 0.0
        next_funding = 0.0
        next_metadata = 0.0
        # OI 历史只在启动时拉一次：用来回填 1h 变化率窗口的起点
        next_oi_history = 0.0

        while not self._stop.is_set():
            now = self._monotonic()
            if now >= next_oi:
                await self._guard(self._poll_open_interest())
                next_oi = now + oi_interval
            if now >= next_oi_history:
                for symbol in self._symbols:
                    await self._guard(self._poll_open_interest_history(symbol))
                next_oi_history = now + metadata_interval_seconds()
            if now >= next_funding:
                await self._guard(self._poll_funding())
                next_funding = now + funding_interval
            if now >= next_book:
                await self._guard(self._poll_orderbook())
                next_book = now + book_interval
            if now >= next_metadata:
                await self._guard(self._poll_instrument_metadata())
                next_metadata = now + metadata_interval_seconds()
            if await self._sleep_or_stop(1.0):
                return

    async def _guard(self, job: Awaitable[None]) -> None:
        """执行单个轮询任务；异常只记日志，不终止轮询线。"""
        try:
            await job
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, RateLimitedError, ValueError) as exc:
            self._rest_failures += 1
            logger.warning(
                "衍生品 REST 请求失败",
                market_type=self._market_type,
                error=f"{type(exc).__name__}: {exc}",
                consecutive_failures=self._rest_failures,
            )
            if self._rest_failures == REST_FAILURE_THRESHOLD:
                await self._record_stale(
                    symbol=self._symbols[0] if self._symbols else "",
                    error=str(exc),
                )
        except Exception as exc:  # 未知异常也不能让轮询永久停止
            logger.error(
                "衍生品轮询异常",
                market_type=self._market_type,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def _poll_open_interest(self) -> None:
        """轮询持仓量当前值。

        顺带做通路 C 的陈旧判定（29.4.3）：OI 是轮询数据，没有 WS 可对照，
        用「业务时间（响应里的 time）vs 拉取时间」判断是否已陈旧。
        """
        for symbol in self._symbols:
            fetched_at = self._clock()
            body = await self._rest.fetch_open_interest(
                market_type=self._market_type, symbol=symbol
            )
            row = parse_open_interest(body, market_type=self._market_type)
            await asyncio.to_thread(self._write_oi, row)
            metrics.market_derivatives_oi_written_total.labels(
                symbol=symbol, market_type=self._market_type
            ).inc()
            self._rest_failures = 0
            await self._check_oi_staleness(symbol, row["ts"], fetched_at)

    async def _check_oi_staleness(
        self, symbol: str, business_ts: dt.datetime, fetched_at: dt.datetime
    ) -> None:
        """OI 陈旧判定（29.4.3 通路 C）。"""
        check = evaluate_staleness(
            business_ts,
            fetched_at=fetched_at,
            threshold_seconds=OI_STALE_THRESHOLD_SECONDS,
        )
        metrics.market_derivatives_stale_seconds.labels(
            symbol=symbol, market_type=self._market_type, dataset="open_interest"
        ).set(check.staleness_seconds)
        if not check.stale:
            return
        event = build_stale_event(
            market_type=self._market_type,
            symbol=symbol,
            check=check,
            detected_at=fetched_at,
        )
        await asyncio.to_thread(self._write_events, [event])
        logger.warning(
            "持仓量数据陈旧",
            symbol=symbol,
            staleness_seconds=round(check.staleness_seconds, 1),
        )

    async def _poll_open_interest_history(self, symbol: str) -> None:
        """拉取持仓量历史（周期 5m），用于回填 1h 变化率窗口。

        OI 当前值只能从轮询拿到，进程刚启动时本地没有历史，
        ``DER_OI_SURGE`` 的 1h 变化率无法计算；历史接口一次性补齐这段窗口。
        """
        body = await self._rest.fetch_open_interest_history(
            market_type=self._market_type, symbol=symbol, period="5m"
        )
        rows = parse_open_interest_history(body, market_type=self._market_type)
        await asyncio.to_thread(self._write_oi_rows, rows)
        metrics.market_derivatives_oi_written_total.labels(
            symbol=symbol, market_type=self._market_type
        ).inc(len(rows))

    async def _poll_funding(self) -> None:
        """轮询资金费率历史，并记录当前预估费率。"""
        for symbol in self._symbols:
            body = await self._rest.fetch_funding_rate(market_type=self._market_type, symbol=symbol)
            for item in body:
                row = parse_funding_rate(item, market_type=self._market_type)
                if row["mark_price"] is None:
                    # markPrice 缺失时用 premiumIndex 回退；仍缺失则跳过该条
                    row["mark_price"] = await self._resolve_mark_price(symbol)
                if row["mark_price"] is None:
                    continue
                await asyncio.to_thread(self._write_funding, row)
            metrics.market_derivatives_funding_written_total.labels(
                symbol=symbol, market_type=self._market_type
            ).inc(len(body))
            self._rest_failures = 0

    async def _resolve_mark_price(self, symbol: str) -> Any:
        """用 ``premiumIndex`` 回退缺失的标记价格。"""
        try:
            body = await self._rest.fetch_premium_index(
                market_type=self._market_type, symbol=symbol
            )
        except (httpx.HTTPError, RateLimitedError, ValueError):
            return None
        parsed = parse_premium_index(body, market_type=self._market_type)
        return parsed["mark_price"]

    async def _poll_orderbook(self) -> None:
        """轮询盘口深度快照（只存 top20，27.3）。"""
        for symbol in self._symbols:
            captured_at = self._clock()
            body = await self._rest.fetch_depth(
                market_type=self._market_type,
                symbol=symbol,
                limit=self._settings.MARKET_BOOK_DEPTH_LEVELS,
            )
            row = parse_depth_snapshot(
                body,
                symbol=symbol,
                market_type=self._market_type,
                captured_at=captured_at,
                depth_levels=self._settings.MARKET_BOOK_DEPTH_LEVELS,
            )
            await asyncio.to_thread(self._write_orderbook, row)
            metrics.market_derivatives_book_written_total.labels(
                symbol=symbol, market_type=self._market_type
            ).inc()
            self._rest_failures = 0

    async def _poll_instrument_metadata(self) -> None:
        """拉取合约规格（启动时 + 每日，29.2）。"""
        body = await self._rest.fetch_exchange_info(market_type=self._market_type)
        rows = parse_instrument_metadata_list(body, market_type=self._market_type)
        await asyncio.to_thread(self._write_metadata, rows)

    # ------------------------------------------------------------------ 流线

    async def _ws_loop(self) -> None:
        """WS 流线：聚合成交 + 标记价格。带指数退避重连（29.3.1）。"""
        while not self._stop.is_set():
            try:
                await self._run_connection()
                reason = "rotated_or_closed"
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, TimeoutError, OSError) as exc:
                reason = type(exc).__name__
            except Exception as exc:
                reason = f"error:{type(exc).__name__}"
                logger.error("衍生品 WS 异常", market_type=self._market_type, error=str(exc))
            delay = reconnect_delay_seconds(
                self._attempt, max_ms=self._settings.MARKET_WS_RECONNECT_MAX_MS
            )
            self._attempt += 1
            logger.warning(
                "衍生品 WS 断开，准备重连",
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
            connected_at = self._monotonic()
            logger.info(
                "衍生品 WS 已连接",
                market_type=self._market_type,
                streams=len(self._symbols) * 2,
            )
            metrics.market_ws_connections_total.labels(market_type=self._market_type).inc()
            self._attempt = 0
            metrics.market_ws_connected.labels(market_type=self._market_type).set(1)
            try:
                await self._consume(ws, connected_at)
            finally:
                metrics.market_ws_connected.labels(market_type=self._market_type).set(0)

    async def _consume(self, ws: ClientConnection, connected_at: float) -> None:
        while not self._stop.is_set():
            if self._monotonic() - connected_at >= MAX_CONNECTION_AGE_SECONDS:
                logger.info("衍生品连接达到存活上限，主动轮换", market_type=self._market_type)
                return
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=HALF_OPEN_TIMEOUT_SECONDS)
            except TimeoutError as exc:
                raise TimeoutError("30 秒无消息，判定半开") from exc
            await self._handle_message(raw)

    async def _handle_message(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("衍生品 WS 消息不是合法 JSON", market_type=self._market_type)
            return
        received_at = self._clock()
        try:
            trade = parse_agg_trade_message(
                message, market_type=self._market_type, received_at=received_at
            )
        except ValueError as exc:
            logger.warning("聚合成交解析失败", market_type=self._market_type, error=str(exc))
            return
        if trade is not None:
            await asyncio.to_thread(self._write_trade, trade.row())
            metrics.market_derivatives_trade_events_total.labels(
                symbol=trade.symbol, market_type=self._market_type
            ).inc()
            return
        try:
            mark = parse_mark_price_message(
                message, market_type=self._market_type, received_at=received_at
            )
        except ValueError as exc:
            logger.warning("标记价格解析失败", market_type=self._market_type, error=str(exc))
            return
        if mark is not None:
            # markPrice 流是当前预估资金费率的低延迟触发源（29.2），
            # 但 funding_history 仍以 REST 为权威——这里只做观测。
            self._mark_prices[mark.symbol] = mark
            metrics.market_derivatives_funding_current_rate.labels(
                symbol=mark.symbol, market_type=self._market_type
            ).set(float(mark.funding_rate))
            metrics.market_derivatives_mark_price.labels(
                symbol=mark.symbol, market_type=self._market_type
            ).set(float(mark.mark_price))

    # ------------------------------------------------------------------ 写入

    def _write_oi(self, row: dict[str, Any]) -> None:
        with self._db() as db:
            outcome = persist_open_interest(db, row)
        logger.debug("持仓量已写入", symbol=row["symbol"], outcome=outcome.value)

    def _write_oi_rows(self, rows: list[dict[str, Any]]) -> None:
        with self._db() as db:
            for row in rows:
                persist_open_interest(db, row)

    def _write_funding(self, row: dict[str, Any]) -> None:
        with self._db() as db:
            outcome = persist_funding_rate(db, row)
        logger.debug("资金费率已写入", symbol=row["symbol"], outcome=outcome.value)

    def _write_orderbook(self, row: dict[str, Any]) -> None:
        with self._db() as db:
            outcome = persist_orderbook(db, row)
        logger.debug("盘口快照已写入", symbol=row["symbol"], outcome=outcome.value)

    def _write_trade(self, row: dict[str, Any]) -> None:
        with self._db() as db:
            outcome = persist_trade(db, row)
        if outcome is IngestOutcome.INSERTED:
            metrics.market_derivatives_trades_total.labels(
                symbol=row["symbol"], market_type=self._market_type
            ).inc()

    def _write_metadata(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self._db() as db:
            for row in rows:
                stmt = (
                    insert(InstrumentMetadata)
                    .values(**row)
                    .on_conflict_do_update(
                        index_elements=["exchange", "market_type", "symbol"],
                        set_={
                            "price_precision": row["price_precision"],
                            "quantity_precision": row["quantity_precision"],
                            "tick_size": row["tick_size"],
                            "step_size": row["step_size"],
                            "min_qty": row["min_qty"],
                            "min_notional": row["min_notional"],
                            "quote_asset": row["quote_asset"],
                            "margin_asset": row["margin_asset"],
                            "onboard_date": row["onboard_date"],
                            "raw": row["raw"],
                        },
                    )
                )
                db.execute(stmt)

    async def _record_stale(self, *, symbol: str, error: str) -> None:
        """记录一次 ``DATA_STALE`` 质量事件（REST 连续失败）。

        连续失败意味着轮询数据已经陈旧，因此直接构造 STALE 事件，
        而不是走「业务时间 vs 拉取时间」的比较（那条路径需要真实业务时间戳）。
        """
        detected_at = self._clock()
        event = build_stale_event(
            market_type=self._market_type,
            symbol=symbol,
            check=StaleCheck(
                stale=True,
                staleness_seconds=0.0,
                threshold_seconds=OI_STALE_THRESHOLD_SECONDS,
            ),
            detected_at=detected_at,
        )
        await asyncio.to_thread(self._write_events, [event])
        logger.error("衍生品 REST 连续失败，判定数据不可用", error=error)

    def _write_events(self, events: list[Any]) -> None:
        with self._db() as db:
            db.add_all(events)
        for event in events:
            metrics.market_data_quality_events_total.labels(
                rule_id=event.rule_id, severity=event.severity
            ).inc()


def metadata_interval_seconds() -> float:
    """合约规格刷新间隔：24 小时（29.2「启动时 + 每日」）。"""
    return 24 * 3600.0


__all__ = [
    "FUNDING_HISTORY_STALE_THRESHOLD_SECONDS",
    "FUNDING_STALE_THRESHOLD_SECONDS",
    "OI_STALE_THRESHOLD_SECONDS",
    "REST_FAILURE_THRESHOLD",
    "DerivativesCollector",
    "metadata_interval_seconds",
]
