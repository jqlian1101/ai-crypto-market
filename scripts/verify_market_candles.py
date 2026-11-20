"""market_candles 数据校验（可重复执行，只读）。

检查项：
1. 未收盘 K 线（close_time > now）必须为 0；
2. OHLC / 非负量非法行必须为 0；
3. 来源必须是 REST_BACKFILL（本脚本只校验补数写入的数据）；
4. 相邻 open_time 差值必须等于周期（无缺口）；
5. 可选 --sample N：抽取每组最新 N 根，与 Binance 公共接口逐字段比对。

退出码：0 全部通过；1 存在失败项。
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text

from aicrypto.common.clock import now
from aicrypto.config.settings import Settings, get_settings
from aicrypto.infrastructure.binance_rest import ENDPOINTS, TIMEFRAME_MS
from aicrypto.infrastructure.database.session import session_scope
from aicrypto.infrastructure.logging import get_logger

logger = get_logger(__name__)

EXPECTED_SOURCE = "REST_BACKFILL"

GROUP_SQL = text(
    """
    SELECT market_type, symbol, timeframe,
           count(*) AS rows,
           count(DISTINCT open_time) AS distinct_open,
           min(open_time) AS first_open,
           max(open_time) AS last_open,
           count(*) FILTER (WHERE close_time > :now) AS not_closed,
           count(*) FILTER (
               WHERE high < greatest(open, close, low) OR high < low
                  OR volume < 0 OR quote_volume < 0
           ) AS bad_ohlc,
           count(*) FILTER (WHERE source <> :source) AS wrong_source
    FROM market_candles
    WHERE exchange = 'binance'
    GROUP BY market_type, symbol, timeframe
    ORDER BY market_type, symbol, timeframe
    """
)

GAP_SQL = text(
    """
    WITH x AS (
        SELECT open_time,
               lead(open_time) OVER (ORDER BY open_time) - open_time AS d
        FROM market_candles
        WHERE exchange = 'binance' AND market_type = :mt
          AND symbol = :sym AND timeframe = :tf
    )
    SELECT count(*) FROM x
    WHERE d IS NOT NULL AND d <> make_interval(secs => :secs)
    """
)

LATEST_SQL = text(
    """
    SELECT open_time, open, high, low, close, volume, quote_volume, trades_count
    FROM market_candles
    WHERE exchange = 'binance' AND market_type = :mt
      AND symbol = :sym AND timeframe = :tf
    ORDER BY open_time DESC
    LIMIT :n
    """
)

FIELDS = ("open", "high", "low", "close", "volume", "quote_volume", "trades_count")


def check_groups(settings: Settings) -> tuple[list[dict[str, Any]], int]:
    """返回各组统计及失败项数量。"""
    failures = 0
    results: list[dict[str, Any]] = []
    with session_scope(settings) as session:
        groups = session.execute(GROUP_SQL, {"now": now(), "source": EXPECTED_SOURCE}).mappings()
        for g in groups:
            gaps = session.execute(
                GAP_SQL,
                {
                    "mt": g["market_type"],
                    "sym": g["symbol"],
                    "tf": g["timeframe"],
                    "secs": TIMEFRAME_MS[g["timeframe"]] // 1000,
                },
            ).scalar_one()
            item = dict(g) | {"gaps": gaps}
            problems = []
            if g["distinct_open"] != g["rows"]:
                problems.append("重复 open_time")
            if g["not_closed"]:
                problems.append(f"未收盘 {g['not_closed']} 行")
            if g["bad_ohlc"]:
                problems.append(f"非法 OHLC/量 {g['bad_ohlc']} 行")
            if g["wrong_source"]:
                problems.append(f"来源非 {EXPECTED_SOURCE} {g['wrong_source']} 行")
            if gaps:
                problems.append(f"缺口 {gaps} 处")
            item["problems"] = problems
            failures += len(problems)
            results.append(item)
    return results, failures


def check_sample(settings: Settings, sample: int) -> tuple[int, int]:
    """与 Binance 逐字段比对每组最新 sample 根。返回 (比对根数, 不一致根数)。"""
    compared = 0
    mismatches = 0
    with session_scope(settings) as session, httpx.Client(timeout=15.0) as client:
        groups = session.execute(
            text(
                "SELECT DISTINCT market_type, symbol, timeframe FROM market_candles "
                "WHERE exchange = 'binance' ORDER BY 1, 2, 3"
            )
        ).all()
        for mt, sym, tf in groups:
            rows = (
                session.execute(LATEST_SQL, {"mt": mt, "sym": sym, "tf": tf, "n": sample})
                .mappings()
                .all()
            )
            if not rows:
                continue
            base_url, path = ENDPOINTS[mt]
            start_ms = int(min(r["open_time"] for r in rows).timestamp() * 1000)
            resp = client.get(
                f"{base_url}{path}",
                params={"symbol": sym, "interval": tf, "startTime": start_ms, "limit": sample + 5},
            )
            resp.raise_for_status()
            remote = {int(k[0]): k for k in resp.json()}
            for r in rows:
                key = int(r["open_time"].timestamp() * 1000)
                compared += 1
                raw = remote.get(key)
                if raw is None:
                    mismatches += 1
                    logger.error("样本在 Binance 不存在", symbol=sym, market_type=mt, timeframe=tf)
                    continue
                remote_vals = {
                    "open": Decimal(raw[1]),
                    "high": Decimal(raw[2]),
                    "low": Decimal(raw[3]),
                    "close": Decimal(raw[4]),
                    "volume": Decimal(raw[5]),
                    "quote_volume": Decimal(raw[7]),
                    "trades_count": int(raw[8]),
                }
                diff = [
                    f
                    for f in FIELDS
                    if (Decimal(str(r[f])) if f != "trades_count" else r[f]) != remote_vals[f]
                ]
                if diff:
                    mismatches += 1
                    logger.error(
                        "样本与 Binance 不一致",
                        symbol=sym,
                        market_type=mt,
                        timeframe=tf,
                        fields=diff,
                    )
    return compared, mismatches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 market_candles 数据（只读）")
    parser.add_argument(
        "--sample", type=int, default=0, help="每组抽样 N 根与 Binance 比对，0 为不比对"
    )
    args = parser.parse_args(argv)
    settings = get_settings()

    results, failures = check_groups(settings)
    for item in results:
        level = logger.error if item["problems"] else logger.info
        level(
            "校验结果",
            market_type=item["market_type"],
            symbol=item["symbol"],
            timeframe=item["timeframe"],
            rows=item["rows"],
            first_open=item["first_open"].isoformat(),
            last_open=item["last_open"].isoformat(),
            gaps=item["gaps"],
            problems=item["problems"] or "OK",
        )

    if args.sample > 0:
        compared, mismatches = check_sample(settings, args.sample)
        logger.info("Binance 样本比对", compared=compared, mismatches=mismatches)
        failures += mismatches

    logger.info("校验结束", groups=len(results), failures=failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
