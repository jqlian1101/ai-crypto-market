"""真实 HTTP socket 端到端验证：超时 -> 部分结果（PRD 24.4）。

**这个脚本验证的是 MockTransport 验不到的东西**：

MockTransport 在进程内直接返回响应，**根本没有真实的 socket**，
所以它验不出「连接建立后服务端真的不回应」这种超时。
生产上最常见的就是这种：网关挂起、连接池半死、
上游模型推理卡住——**请求发出去了，钱也花了，就是没有响应**。

这里起一个**真实的 HTTP 服务器**（真 socket、真 accept、真阻塞）：
客户端连上去，服务器读完请求就**故意不回应**，直到客户端超时断开。
这样触发的就是货真价实的 ``httpx.ReadTimeout``。

跑法::

    python _e2e_timeout_partial.py
"""

from __future__ import annotations

import datetime as dt
import socket
import threading
import time
from contextlib import suppress
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aicrypto.config.settings import Settings
from aicrypto.infrastructure.database.models.analysis import (
    LLMCostRecord,
    ModelPricing,
    ModelRun,
)
from aicrypto.infrastructure.database.models.market import MarketCandle
from aicrypto.infrastructure.database.models.report import (
    AnalysisReportRecord,
    AnalysisRunRecord,
)
from aicrypto.services import analysis_worker

DB_URL = "postgresql+psycopg://aicrypto:aicrypto@127.0.0.1:15432/aicrypto"
MODEL_ID = "e2e-hang-model"
RUN_ID = "e2e_run_timeout_partial"
ANALYSIS_ID = "e2e_analysis_timeout_partial"
SYMBOL = "E2EUSDT"
#: 4h / 1h / 15m / 5m 各造多少根 K 线（够算指标即可）
BARS_PER_FRAME = {"5m": 300, "15m": 300, "1h": 300, "4h": 300}
FRAME_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


class _HangServer:
    """真实 socket 服务器：接受连接、读完请求、然后**永不回应**。"""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.5)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.accepted = 0

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.is_set():
                try:
                    conn, _ = self._sock.accept()
                except (TimeoutError, OSError):
                    continue
                self.accepted += 1
                # 读掉请求头，然后挂住不回——客户端只能等到超时。
                with suppress(OSError):
                    conn.settimeout(2.0)
                    conn.recv(65535)
                # **不 close**：让连接保持打开，制造「服务端收下了但不吭声」

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        with suppress(OSError):
            self._sock.close()


def _seed(db: Session) -> None:
    db.add(
        ModelPricing(
            model_id=MODEL_ID,
            pricing_version="v1",
            provider="cloud",
            input_usd_per_mtok=Decimal("3.0"),
            output_usd_per_mtok=Decimal("15.0"),
            currency="USD",
            effective_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
            created_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        )
    )
    db.add(
        AnalysisRunRecord(
            run_id=RUN_ID,
            analysis_id=ANALYSIS_ID,
            symbol=SYMBOL,
            market_type="SPOT",
            trigger="MANUAL",
            status="QUEUED",
            analysis_mode="LIVE",
            created_at=dt.datetime.now(dt.UTC),
            started_at=dt.datetime.now(dt.UTC),
        )
    )
    # **造真的 K 线**：``build_snapshot`` 从库里读已收盘 K 线，
    # 没有它就抛 ``ANALYSIS_SNAPSHOT_MISSING``——那样根本走不到模型调用。
    base = dt.datetime.now(dt.UTC).replace(microsecond=0)
    for frame, count in BARS_PER_FRAME.items():
        step = FRAME_SECONDS[frame]
        for i in range(count):
            close_time = base - dt.timedelta(seconds=step * (count - i))
            open_time = close_time - dt.timedelta(seconds=step)
            # 造一段带趋势 + 噪声的价格，保证指标/结构/regime 都能算出来
            price = Decimal("100") + Decimal(i % 40) * Decimal("0.25")
            high = price + Decimal("0.4")
            low = price - Decimal("0.4")
            db.add(
                MarketCandle(
                    exchange="binance",
                    market_type="SPOT",
                    symbol=SYMBOL,
                    timeframe=frame,
                    open_time=open_time,
                    close_time=close_time,
                    open=price,
                    high=high,
                    low=low,
                    close=price,
                    volume=Decimal("10"),
                    quote_volume=Decimal("1000"),
                    trades_count=100,
                    taker_buy_base_volume=Decimal("5"),
                    taker_buy_quote_volume=Decimal("500"),
                    exchange_ts=close_time,
                    received_at=close_time,
                    processed_at=close_time,
                    is_closed=True,
                    source="REST",
                )
            )
    db.commit()


def _cleanup(engine) -> None:
    with Session(engine) as db:
        db.execute(
            AnalysisReportRecord.__table__.delete().where(
                AnalysisReportRecord.analysis_id == ANALYSIS_ID
            )
        )
        db.execute(AnalysisRunRecord.__table__.delete().where(AnalysisRunRecord.run_id == RUN_ID))
        db.execute(MarketCandle.__table__.delete().where(MarketCandle.symbol == SYMBOL))
        db.execute(LLMCostRecord.__table__.delete().where(LLMCostRecord.model_id == MODEL_ID))
        db.execute(ModelRun.__table__.delete().where(ModelRun.model_id == MODEL_ID))
        db.execute(ModelPricing.__table__.delete().where(ModelPricing.model_id == MODEL_ID))
        db.commit()


def main() -> int:
    engine = create_engine(DB_URL)
    server = _HangServer()
    server.start()
    print(f"[e2e] 真实 HTTP 服务器已启动: 127.0.0.1:{server.port}（收到请求后不回应）")

    settings = Settings(
        _env_file=None,
        DATABASE_URL=DB_URL,
        REDIS_URL="redis://127.0.0.1:16379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL=f"http://127.0.0.1:{server.port}/v1",
        LLM_CLOUD_API_KEY="e2e-key",
        LLM_TIER_STANDARD_MODEL=MODEL_ID,
        LLM_TIMEOUT_SECONDS=2,
        LLM_MAX_RETRIES=1,
        LLM_SCHEMA_REPAIR_ATTEMPTS=0,
        LLM_ANALYSIS_TOTAL_BUDGET_SECONDS=10,
        LLM_TRUST_ENV=False,
    )

    _cleanup(engine)
    with Session(engine) as db:
        _seed(db)

    print("[e2e] 开始执行 process_one ...")
    started = time.monotonic()
    try:
        ok = analysis_worker.process_one(settings)
    finally:
        elapsed = time.monotonic() - started
        server.stop()
    print(f"[e2e] process_one 返回 {ok}，耗时 {elapsed:.1f}s，服务器收到 {server.accepted} 个连接")

    failures: list[str] = []
    with Session(engine) as db:
        run_row = db.get(AnalysisRunRecord, RUN_ID)
        status = run_row.status if run_row is not None else "<missing>"
        print(f"[e2e] analysis_runs.status = {status}")

        report = db.scalar(
            select(AnalysisReportRecord).where(AnalysisReportRecord.analysis_id == ANALYSIS_ID)
        )
        if report is None:
            failures.append("没有生成报告 —— 超时后应产出部分报告")
        else:
            content = report.content or {}
            print(f"[e2e] report.status = {report.status}")
            print(f"[e2e] report.degraded = {report.degraded}")
            print(f"[e2e] report.degraded_reason = {report.degraded_reason}")
            print(f"[e2e] market_bias = {content.get('market_bias')!r}")
            print(f"[e2e] scenarios = {content.get('scenarios')!r}")
            print(f"[e2e] dominant_factors = {content.get('dominant_factors')!r}")
            print(f"[e2e] time_horizon = {content.get('time_horizon')!r}")
            ev = content.get("evidence") or []
            print(f"[e2e] evidence 条数 = {len(ev)}")
            for item in ev[:6]:
                print(f"          - {item}")

            # ---- 不伪造断言（PRD 32.4）----
            if status != "PARTIAL":
                failures.append(f"运行状态应为 PARTIAL，实际 {status}")
            if report.status != "PARTIAL":
                failures.append(f"报告状态应为 PARTIAL，实际 {report.status}")
            if report.degraded is not True:
                failures.append("报告应标记 degraded=True")
            if content.get("market_bias") != "UNAVAILABLE":
                failures.append(
                    f"market_bias 必须为 UNAVAILABLE，实际 {content.get('market_bias')!r}"
                )
            if content.get("scenarios"):
                failures.append("场景必须为空（不得伪造）")
            if content.get("scenario_probabilities") is not None:
                failures.append("scenario_probabilities 必须为 None（不得伪造）")
            if content.get("dominant_factors"):
                failures.append("dominant_factors 必须为空（不得伪造）")
            if not ev:
                failures.append("部分结果必须携带已计算出的确定性证据")

        runs = list(db.scalars(select(ModelRun).where(ModelRun.model_id == MODEL_ID)))
        print(f"[e2e] model_runs = {len(runs)} 行，状态 = {[r.status for r in runs]}")
        if not runs:
            failures.append("计量记录为空 —— 超时尝试也必须记账（L-24）")

    _cleanup(engine)
    engine.dispose()

    print()
    if failures:
        print("[e2e] 失败：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("[e2e] 全部通过：超时 -> PARTIAL 部分结果，且未伪造结论，计量存活。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
