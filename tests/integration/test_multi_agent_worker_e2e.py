"""多 Agent 路径的**端到端**验证：从「待处理的任务」到「报告发布」。

**为什么必须端到端**：
前面那些测试分别覆盖了图（拓扑与纪律）、模型调用、落库、报告转换。
但它们**各自**都从中间切入——
中间那几个接缝（worker -> 编排 -> 计量 -> 落库 -> 发布）**从没被一起走过**。

而接缝恰恰是最容易出问题的地方：
- 参数传不到（档位就算好了却没用上——L-35 就是这么来的）；
- 事务边界搞错（计量随报告一起回滚）；
- 失败路径漏写状态（任务永远卡在 RUNNING）。

**这条路径此前完全没有测试**——
``process_one_multi`` 从未被任何用例调用过。
"""

from __future__ import annotations

import datetime as dt
import json
import os
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aicrypto.common.clock import now
from aicrypto.config.settings import Settings
from aicrypto.infrastructure.database.models.analysis import (
    AgentOutput as AgentOutputRow,
)
from aicrypto.infrastructure.database.models.analysis import (
    AgentRun,
    LLMCostRecord,
    ModelPricing,
    ModelRun,
)
from aicrypto.infrastructure.database.models.market import MarketCandle
from aicrypto.infrastructure.database.models.report import AnalysisRunRecord
from aicrypto.services import analysis_worker

pytestmark = pytest.mark.integration

AS_OF = dt.datetime(2026, 11, 27, 0, 0, tzinfo=dt.UTC)

#: 各 Agent system 提示的**特征开头**——用它识别「这次请求是谁发的」。
AGENT_PROMPT_MARKERS: dict[str, str] = {
    "technical": "你是加密市场的技术面分析 Agent",
    "structure": "你是加密市场的市场结构分析 Agent",
    "derivatives": "你是加密市场的衍生品分析 Agent",
    "liquidity": "你是加密市场的流动性分析 Agent",
    "regime": "你是加密市场的市场状态（Regime）分析 Agent",
    "risk": "你是加密市场的事件与风险分析 Agent",
    "master": "你是加密市场的 Trading Master Agent",
}


def _identify_agent(body: dict) -> str:
    system = body["messages"][0]["content"]
    for name, marker in AGENT_PROMPT_MARKERS.items():
        if marker in system:
            return name
    raise AssertionError(f"无法识别 Agent，system 提示开头：{system[:80]!r}")


def _agent_payload(agent_name: str) -> dict:
    """一份合法的 Agent 输出。"""
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "agent_name": agent_name,
                            "agent_version": "v1",
                            "model_id": "test-model",
                            "prompt_version": "v1",
                            "market_snapshot_id": "snap_e2e",
                            "as_of": AS_OF.isoformat(),
                            "time_horizon": "4h",
                            "summary": "整体偏多，但存在结构性分歧。",
                            "findings": [
                                "【事实】价格站上 200 日均线",
                                "【推断】资金费率转正说明多头情绪回暖",
                                "【假设】若成交量持续放大则可能启动突破",
                            ],
                            "supporting_evidence": ["indicators.1h.rsi"],
                            "confidence": 0.6,
                            "scenario_probabilities": {"up": 0.5, "down": 0.3, "flat": 0.2},
                        },
                        ensure_ascii=False,
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 800, "completion_tokens": 200},
    }


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        agent_name = _identify_agent(body)
        return httpx.Response(200, json=_agent_payload(agent_name), request=request)

    return httpx.MockTransport(handler)


@pytest.fixture
def db_env():
    url = os.getenv("ANALYSIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 ANALYSIS_TEST_DATABASE_URL 以执行 PostgreSQL 集成测试")
    engine = create_engine(url)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        engine.dispose()


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_URL=os.getenv("ANALYSIS_TEST_DATABASE_URL", ""),
        REDIS_URL="redis://127.0.0.1:6379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL="https://example.invalid/v1",
        LLM_CLOUD_API_KEY="test-key",
        LLM_TIER_LIGHT_MODEL="test-model",
        LLM_TIER_STANDARD_MODEL="test-model",
        LLM_TIER_DEEP_MODEL="test-model",
    )


def _seed(db: Session) -> str:
    """备好一笔待处理的任务 + 足够的 K 线。"""
    moment = now()
    db.add(
        ModelPricing(
            model_id="test-model",
            pricing_version="v1",
            provider="cloud",
            input_usd_per_mtok=Decimal("3.0"),
            output_usd_per_mtok=Decimal("15.0"),
            currency="USD",
            effective_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
            created_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        )
    )
    for i in range(200):
        ts = moment - dt.timedelta(hours=200 - i)
        db.add(
            MarketCandle(
                exchange="binance",
                symbol="E2EMULTIUSDT",
                market_type="SPOT",
                timeframe="1h",
                open_time=ts - dt.timedelta(hours=1),
                close_time=ts,
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("90"),
                close=Decimal("105"),
                volume=Decimal("5"),
                quote_volume=Decimal("500"),
                trades_count=10,
                exchange_ts=ts - dt.timedelta(hours=1),
                received_at=ts - dt.timedelta(minutes=59),
                processed_at=ts - dt.timedelta(minutes=58),
                is_closed=True,
                source="REST",
            )
        )
    run_id = "run_e2e_multi"
    db.add(
        AnalysisRunRecord(
            run_id=run_id,
            analysis_id="ana_e2e_multi",
            symbol="E2EMULTIUSDT",
            market_type="SPOT",
            trigger="MANUAL",
            status="QUEUED",
            analysis_mode="LIVE",
            created_at=moment,
        )
    )
    db.flush()
    return run_id


def test_process_one_multi_publishes_report_end_to_end(
    db_env: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完整走一遍：**待处理任务 -> 报告发布**。

    断言的关键点（每一个都对应一条纪律）：

    1. 任务被认领并走到终态（不是卡在 RUNNING）；
    2. 报告**真的发布了**，且内容来自 Master；
    3. ``agent_runs`` 7 行 / ``agent_outputs`` 7 行；
    4. 计量 7 行，**按 Agent 区分**（成本报表要能回答「哪个 Agent 最贵」）；
    5. 报告的 ``versions.agent_mode == "multi"``；
    6. 事实/推断/假设的标注**保留在报告里**。
    """
    settings = _settings()
    run_id = _seed(db_env)

    @contextmanager
    def scope(_settings: Any):
        yield db_env

    monkeypatch.setattr(analysis_worker, "session_scope", scope)
    monkeypatch.setattr(analysis_worker, "report_scope", scope)
    monkeypatch.setattr(analysis_worker, "metering_scope", scope)

    original_client = httpx.Client

    def patched_client(*args: Any, **kwargs: Any):
        kwargs["transport"] = _mock_transport()
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        assert analysis_worker.process_one_multi(settings)
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    db_env.flush()

    # ---- 1. 任务走到终态 ----
    run = db_env.get(AnalysisRunRecord, run_id)
    assert run is not None
    assert run.status in ("COMPLETE", "PARTIAL"), f"任务状态异常：{run.status}"

    # ---- 2/5/6. 报告发布 ----
    from aicrypto.infrastructure.database.models.report import AnalysisReportRecord

    report = db_env.scalars(
        select(AnalysisReportRecord).where(AnalysisReportRecord.analysis_id == "ana_e2e_multi")
    ).first()
    assert report is not None, "报告必须真的发布"

    versions = report.versions
    assert versions.get("agent_mode") == "multi", "必须标明走的是多 Agent 路径"

    content = report.content
    joined = " ".join(content.get("dominant_factors", []) + content.get("disagreements", []))
    assert "【事实】" in joined, "事实/推断/假设的标注必须保留在报告里"
    assert "【假设】" in joined, "假设必须留在分歧栏（它是未验证的推测）"

    # ---- 3. 落库 ----
    runs = list(db_env.scalars(select(AgentRun)))
    outputs = list(db_env.scalars(select(AgentOutputRow)))
    assert len(runs) == 7, f"应有 7 行 agent_runs，实际 {len(runs)}"
    assert len(outputs) == 7, f"应有 7 行 agent_outputs，实际 {len(outputs)}"

    # ---- 4. 计量 ----
    model_runs = list(db_env.scalars(select(ModelRun)))
    cost_records = list(db_env.scalars(select(LLMCostRecord)))
    assert len(model_runs) == 7, f"应有 7 次调用的计量，实际 {len(model_runs)}"
    assert len(cost_records) == 7

    agent_names = {row.agent_name for row in cost_records}
    assert len(agent_names) == 7, f"计量必须按 Agent 区分，实际 {agent_names}"

    # agent_run_id 必须接上（L-33 修的那条链）
    linked = [row for row in model_runs if row.agent_run_id]
    assert len(linked) == 7, "每次调用的 agent_run_id 都必须接上 agent_runs"


def test_process_one_multi_fails_loudly_when_master_fails(
    db_env: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Master 失败 → **不发布报告**，任务标记为 FAILED。

    **不能拿子 Agent 的输出拼一份报告顶上去**——
    那会让读者以为「多 Agent 达成了一致」，而实际上没人做过综合。
    """
    settings = _settings()
    _seed(db_env)

    @contextmanager
    def scope(_settings: Any):
        yield db_env

    monkeypatch.setattr(analysis_worker, "session_scope", scope)
    monkeypatch.setattr(analysis_worker, "report_scope", scope)
    monkeypatch.setattr(analysis_worker, "metering_scope", scope)

    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        agent_name = _identify_agent(body)
        if agent_name == "master":
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": "not json"}},
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1},
                },
                request=request,
            )
        return httpx.Response(200, json=_agent_payload(agent_name), request=request)

    def patched_client(*args: Any, **kwargs: Any):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        assert analysis_worker.process_one_multi(settings)
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    db_env.flush()

    from aicrypto.infrastructure.database.models.report import AnalysisReportRecord

    report = db_env.scalars(
        select(AnalysisReportRecord).where(AnalysisReportRecord.analysis_id == "ana_e2e_multi")
    ).first()
    assert report is None, "Master 失败时不得发布报告"

    # 子 Agent 的结果**照常留痕**——它们真的花了钱、真的算出来了。
    runs = list(db_env.scalars(select(AgentRun)))
    assert len(runs) == 7, "子 Agent 的结果必须留痕"
    master_runs = [run for run in runs if run.agent_name == "master"]
    assert master_runs and master_runs[0].status == "FAILED"
