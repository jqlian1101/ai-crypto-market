"""历史 K 线补数（PRD 第 27.2 / 29.4 章）。

从 Binance 公共 REST 接口分页拉取已收盘 K 线，幂等 upsert 到 ``market_candles``。

约定：
- 只写已收盘 K 线（close_time <= now），未收盘数据直接丢弃；
- source 固定为 ``REST_BACKFILL``；
- 可重复执行：主键冲突时覆盖为 REST 数据（REST 为准，29.4）；
- 支持 ``--dry-run``，只拉取与校验，不写库；
- 退出码：0 成功，1 存在失败。
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Iterator
from datetime import UTC, timedelta
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert

from aicrypto.common.clock import now
from aicrypto.config.settings import Settings, get_settings
from aicrypto.infrastructure.binance_rest import (
    ENDPOINTS,
    TIMEFRAME_MS,
    is_valid_ohlc,
    parse_kline,
)
from aicrypto.infrastructure.database.models.market import MarketCandle
from aicrypto.infrastructure.database.session import session_scope
from aicrypto.infrastructure.logging import get_logger

logger = get_logger(__name__)

SOURCE = "REST_BACKFILL"
PAGE_LIMIT = 1000
REQUEST_INTERVAL_SECONDS = 0.25  # 简单限速，远低于 Binance 权重上限

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


def fetch_pages(
    client: httpx.Client,
    *,
    market_type: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> Iterator[list[list[Any]]]:
    """按 openTime 升序分页拉取，直到覆盖 end_ms。"""
    base_url, path = ENDPOINTS[market_type]
    interval_ms = TIMEFRAME_MS[timeframe]
    cursor = start_ms
    while cursor < end_ms:
        resp = client.get(
            f"{base_url}{path}",
            params={
                "symbol": symbol,
                "interval": timeframe,
                "startTime": cursor,
                "endTime": end_ms - 1,  # Binance endTime 为闭区间，排除未收盘的那根
                "limit": PAGE_LIMIT,
            },
        )
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        yield page
        last_open = int(page[-1][0])
        next_cursor = last_open + interval_ms
        if next_cursor <= cursor:
            break  # 防御：避免死循环
        cursor = next_cursor
        time.sleep(REQUEST_INTERVAL_SECONDS)


def upsert_rows(settings: Settings, rows: list[dict[str, Any]]) -> int:
    """幂等写入；主键冲突时以 REST 数据覆盖。"""
    if not rows:
        return 0
    stmt = insert(MarketCandle.__table__).values(rows)
    update_cols = {col: stmt.excluded[col] for col in _UPDATE_COLUMNS}
    stmt = stmt.on_conflict_do_update(
        constraint="pk_market_candles",
        set_=update_cols,
    )
    with session_scope(settings) as session:
        session.execute(stmt)
    return len(rows)


def run(args: argparse.Namespace, settings: Settings) -> int:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    market_types = [m.strip().upper() for m in args.market_types.split(",") if m.strip()]
    timeframes = [t.strip().lower() for t in args.timeframes.split(",") if t.strip()]

    for mt in market_types:
        if mt not in ENDPOINTS:
            logger.error("market_type 非法", market_type=mt)
            return 1
    for tf in timeframes:
        if tf not in TIMEFRAME_MS:
            logger.error("timeframe 非法", timeframe=tf)
            return 1

    end_dt = now().astimezone(UTC)
    start_dt = end_dt - timedelta(days=args.days)
    fetched_at = now()
    failures = 0
    total = 0

    with httpx.Client(timeout=15.0) as client:
        for mt in market_types:
            for symbol in symbols:
                for tf in timeframes:
                    interval_ms = TIMEFRAME_MS[tf]
                    # 只取已收盘：end 对齐到当前周期起点，区间右端为最后一根已收盘 K 线的 openTime+1
                    end_ms = (int(end_dt.timestamp() * 1000) // interval_ms) * interval_ms
                    start_ms = int(start_dt.timestamp() * 1000)
                    start_ms = (start_ms // interval_ms) * interval_ms
                    try:
                        rows: list[dict[str, Any]] = []
                        for page in fetch_pages(
                            client,
                            market_type=mt,
                            symbol=symbol,
                            timeframe=tf,
                            start_ms=start_ms,
                            end_ms=end_ms,
                        ):
                            for raw in page:
                                row = parse_kline(
                                    raw,
                                    symbol=symbol,
                                    market_type=mt,
                                    timeframe=tf,
                                    fetched_at=fetched_at,
                                    source=SOURCE,
                                )
                                # 未收盘 K 线不落库（29.5.1）
                                if row["close_time"] > end_dt:
                                    continue
                                if not is_valid_ohlc(row):
                                    logger.warning(
                                        "K 线 OHLC 非法，已拒绝",
                                        symbol=symbol,
                                        market_type=mt,
                                        timeframe=tf,
                                        open_time=row["open_time"].isoformat(),
                                    )
                                    failures += 1
                                    continue
                                rows.append(row)

                        if args.dry_run:
                            logger.info(
                                "dry-run 拉取完成",
                                symbol=symbol,
                                market_type=mt,
                                timeframe=tf,
                                rows=len(rows),
                            )
                        else:
                            written = upsert_rows(settings, rows)
                            total += written
                            logger.info(
                                "补数完成",
                                symbol=symbol,
                                market_type=mt,
                                timeframe=tf,
                                rows=written,
                            )
                    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                        failures += 1
                        logger.error(
                            "补数失败",
                            symbol=symbol,
                            market_type=mt,
                            timeframe=tf,
                            error=str(exc),
                        )

    logger.info("补数结束", total_rows=total, failures=failures, dry_run=args.dry_run)
    return 1 if failures else 0


def build_parser(settings: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Binance K 线历史补数（幂等）")
    parser.add_argument("--symbols", default=settings.MARKET_SYMBOLS)
    parser.add_argument("--market-types", default=settings.MARKET_TYPES)
    parser.add_argument("--timeframes", default=settings.MARKET_TIMEFRAMES)
    parser.add_argument("--days", type=int, default=7, help="向前补的天数，默认 7")
    parser.add_argument("--dry-run", action="store_true", help="只拉取与校验，不写库")
    return parser


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    args = build_parser(settings).parse_args(argv)
    if args.days < 1:
        logger.error("--days 必须 >= 1", days=args.days)
        return 1
    return run(args, settings)


if __name__ == "__main__":
    sys.exit(main())
