"""获取 Binance 公共 K 线并验证一次真实分析：从项目根目录运行。"""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from aicrypto.common.clock import now
from aicrypto.config.settings import get_settings
from aicrypto.infrastructure.database.models.market import MarketCandle
from aicrypto.infrastructure.database.models.report import AnalysisRunRecord
from aicrypto.infrastructure.database.session import session_scope
from aicrypto.infrastructure.proxy import httpx_client_kwargs
from aicrypto.services.analysis import create_run, get_report, get_run
from aicrypto.services.analysis_worker import process_one


def main() -> None:
    settings = get_settings()
    with session_scope(settings) as db:
        pending = db.scalar(
            select(func.count())
            .select_from(AnalysisRunRecord)
            .where(AnalysisRunRecord.status.in_(["QUEUED", "RUNNING"]))
        )
        if pending:
            raise RuntimeError("已有排队或运行任务，请先处理后再执行联调脚本")
    with httpx.Client(
        base_url="https://data-api.binance.vision",
        timeout=30,
        **httpx_client_kwargs(settings.MARKET_PROXY),
    ) as client:
        for frame in ("5m", "15m", "1h", "4h"):
            response = client.get(
                "/api/v3/klines",
                params={
                    "symbol": "BTCUSDT",
                    "interval": frame,
                    "limit": 51,
                },
            )
            response.raise_for_status()
            received = now()
            rows = []
            for item in response.json():
                close_time = datetime.fromtimestamp(item[6] / 1000, UTC)
                if close_time > received:
                    continue
                rows.append(
                    {
                        "exchange": "binance",
                        "market_type": "SPOT",
                        "symbol": "BTCUSDT",
                        "timeframe": frame,
                        "open_time": datetime.fromtimestamp(item[0] / 1000, UTC),
                        "close_time": close_time,
                        "open": Decimal(item[1]),
                        "high": Decimal(item[2]),
                        "low": Decimal(item[3]),
                        "close": Decimal(item[4]),
                        "volume": Decimal(item[5]),
                        "quote_volume": Decimal(item[7]),
                        "trades_count": item[8],
                        "taker_buy_base_volume": Decimal(item[9]),
                        "taker_buy_quote_volume": Decimal(item[10]),
                        "exchange_ts": close_time,
                        "received_at": received,
                        "processed_at": now(),
                        "is_closed": True,
                        "source": "REST_BACKFILL",
                    }
                )
            if not rows:
                raise RuntimeError(f"{frame} 未返回已收盘 K 线")
            with session_scope(settings) as db:
                db.execute(insert(MarketCandle).values(rows).on_conflict_do_nothing())
            print(f"candles: BTCUSDT {frame} {len(rows)} closed", flush=True)
    with session_scope(settings) as db:
        run = create_run(db, symbol="BTCUSDT", market_type="SPOT", trigger="MANUAL", now=now())
    print("run_id:", run["run_id"], flush=True)
    process_one(settings)
    with session_scope(settings) as db:
        state = get_run(db, run["run_id"])
        print("status:", state["status"], "error:", state["error"], flush=True)
        if state["status"] not in ("COMPLETE", "PARTIAL"):
            raise RuntimeError("真实模型报告生成失败，请检查运行错误码")
        report = get_report(db, run["analysis_id"])
        print("analysis_id:", report["analysis_id"], flush=True)
        print("report_path: /analysis/" + report["analysis_id"], flush=True)
        print("summary:", report["content"]["summary"], flush=True)


if __name__ == "__main__":
    main()
