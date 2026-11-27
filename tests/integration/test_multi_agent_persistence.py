"""多 Agent 落库与计量的真库验证（PRD 4.7 / 24.2 / 30.4 / 32.4）。

**背景**��``agent_runs`` / ``agent_outputs`` 两张表早已存在，
但 ``agent_outputs`` **零写入方**——表建了、索引建了，从来没写过。
从「表存在」这个事实看一切正常，这是与 L-30 同族的静默失效。

**这些测试断言三件事**：

1. **成功路径**：6 个子 Agent + 1 个 Master 各写一行 ``agent_runs``，
   且**每个成功 Agent 各写一行** ``agent_outputs``；
2. **失败路径**：失败的 Agent 写 ``agent_runs`` 但**不写** ``agent_outputs``
   （PRD 32.4：绝不伪造输出）；
3. **计量接线**：多 Agent 的调用**必须**出现在 ``model_runs`` /
   ``llm_cost_records`` 里——否则成本报表会漏掉它们，
   而多 Agent 的调用量是单 Agent 的 7 倍，漏掉就是**成本严重低估**。

**为什么必须真库**：
假 Session 能断言「调用了 add()」，但验证不了
**CHECK 约束**（``status`` 是否在允许枚举内）、
**事务边界**（计量行是否真的落库）、
以及**表间引用**（``agent_outputs.run_id`` 能否对上 ``agent_runs.run_id``）。
"""

from __future__ import annotations

import datetime as dt
import json
import os
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aicrypto.config.settings import Settings
from aicrypto.domain.analysis.agent_output import ModelTier
from aicrypto.infrastructure.database.models.analysis import (
    AgentOutput as AgentOutputRow,
)
from aicrypto.infrastructure.database.models.analysis import (
    AgentRun,
    LLMCostRecord,
    ModelPricing,
    ModelRun,
)
from aicrypto.services.agent_registry import MASTER_SPEC, SUB_AGENT_NAMES
from aicrypto.services.multi_agent_runner import run_multi_agent_analysis

pytestmark = pytest.mark.integration

AS_OF = dt.datetime(2026, 11, 27, 0, 0, tzinfo=dt.UTC)


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
                            "market_snapshot_id": "snap_test",
                            "as_of": AS_OF.isoformat(),
                            "time_horizon": "4h",
                            "summary": "证据不足，观望",
                            "findings": ["一条发现"],
                            "supporting_evidence": ["indicators.1h.rsi"],
                            "confidence": 0.5,
                        },
                        ensure_ascii=False,
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 800, "completion_tokens": 200},
    }


#: 各 Agent system 提示的**特征开头**——
#: 用它来识别「这次请求是哪个 Agent 发出的」。
#:
#: **不能用泛化关键词**（比如「衍生品」）——
#: Master 的提示里也列了 ``derivatives`` 这个来源域，
#: 用它匹配会把 Master 的请求误判成 derivatives 的请求。
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
    """从请求体识别发起调用的 Agent。"""
    system = body["messages"][0]["content"]
    for name, marker in AGENT_PROMPT_MARKERS.items():
        if marker in system:
            return name
    raise AssertionError(f"无法识别 Agent，system 提示开头：{system[:80]!r}")


def _mock_transport(payload_for: Any) -> httpx.MockTransport:
    """按请求体里的 agent 名返回不同的 payload。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        agent_name = _identify_agent(body)
        return httpx.Response(200, json=payload_for(agent_name), request=request)

    return httpx.MockTransport(handler)


@pytest.fixture
def db_env():
    """真库环境；未配置测试库则跳过。"""
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


@pytest.fixture
def settings(db_env: Session) -> Settings:
    """依赖 ``db_env`` 是为了**保证先触发 skip**。"""
    return Settings(
        _env_file=None,
        DATABASE_URL=os.getenv("ANALYSIS_TEST_DATABASE_URL", ""),
        REDIS_URL="redis://127.0.0.1:6379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL="https://example.invalid/v1",
        LLM_CLOUD_API_KEY="test-key",
        LLM_TIER_LIGHT_MODEL="test-model",
        LLM_TIER_STANDARD_MODEL="test-model",
        # **DEEP 也要配**——否则请求 DEEP 会降级到 STANDARD，
        # 记录下来的「实际生效档位」就是 STANDARD，
        # 测不出「请求的档位有没有传下去」。
        LLM_TIER_DEEP_MODEL="test-model",
    )


@pytest.fixture
def snapshot() -> dict:
    return {
        "snapshot_id": "snap_test",
        "symbol": "BTCUSDT",
        "as_of": AS_OF.isoformat(),
        "available_as_of": AS_OF.isoformat(),
        "current_price": {"last": 100.0},
        "multi_timeframe_ohlcv": {"1h": []},
        "indicators": {"1h": {"rsi": 55.0}},
        "market_structure": {"1h": {}},
        "volatility_and_volume": {"1h": {}},
        "derivatives": {},
        "liquidity": {},
        "market_regime": {"1h": {"regime": "RANGE"}},
        "data_quality": {"overall": "GOOD"},
    }


def _seed_pricing(db: Session) -> None:
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
    db.flush()


def test_successful_agents_write_agent_runs_and_outputs(
    db_env: Session,
    settings: Settings,
    snapshot: dict,
) -> None:
    """全部成功时：7 行 ``agent_runs``（6 子 + 1 Master）+ 7 行 ``agent_outputs``。"""
    _seed_pricing(db_env)

    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(_agent_payload)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        outcome = run_multi_agent_analysis(
            snapshot=snapshot,
            settings=settings,
            db=db_env,
            analysis_id="ana_test",
            meter_db=db_env,
            record_attempt=_noop_record,
        )
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    db_env.flush()

    runs = list(db_env.scalars(select(AgentRun)))
    outputs = list(db_env.scalars(select(AgentOutputRow)))

    assert len(runs) == 7, f"应有 7 行 agent_runs，实际 {len(runs)}"
    assert len(outputs) == 7, f"应有 7 行 agent_outputs，实际 {len(outputs)}"

    agent_names = {run.agent_name for run in runs}
    assert agent_names == set(SUB_AGENT_NAMES) | {MASTER_SPEC.name}

    # 每行的 agent_outputs 都能对上对应的 agent_runs
    run_ids = {run.run_id for run in runs}
    for output in outputs:
        assert output.run_id in run_ids, "agent_outputs.run_id 必须指向某个 agent_runs"

    assert outcome.succeeded
    assert len(outcome.agent_run_ids) == 7
    assert outcome.missing_agents == []


def test_failed_agent_writes_run_but_no_output(
    db_env: Session,
    settings: Settings,
    snapshot: dict,
) -> None:
    """一个子 Agent 失败：**有** ``agent_runs``，**没有** ``agent_outputs``。

    PRD 32.4：不得伪造输出。
    """
    _seed_pricing(db_env)

    original_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        agent_name = _identify_agent(body)
        if agent_name == "derivatives":
            # 返回**非法 JSON**——模拟 schema 校验失败
            return httpx.Response(
                200,
                json={
                    "choices": [{"finish_reason": "stop", "message": {"content": "not json"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10},
                },
                request=request,
            )
        return httpx.Response(200, json=_agent_payload(agent_name), request=request)

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        outcome = run_multi_agent_analysis(
            snapshot=snapshot,
            settings=settings,
            db=db_env,
            analysis_id="ana_test",
            meter_db=db_env,
            record_attempt=_noop_record,
        )
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    db_env.flush()

    runs = list(db_env.scalars(select(AgentRun)))
    outputs = list(db_env.scalars(select(AgentOutputRow)))

    failed_runs = [run for run in runs if run.status == "FAILED"]
    assert len(failed_runs) == 1, "失败的 Agent 必须留痕"
    assert failed_runs[0].agent_name == "derivatives"

    # 成功的是 6 个（5 子 + 1 Master），失败的不写 output
    assert len(outputs) == 6
    assert len(runs) == 7

    assert outcome.missing_agents == ["derivatives"]
    assert not outcome.succeeded or outcome.master_result is not None


def test_multi_agent_calls_are_metered(
    db_env: Session,
    settings: Settings,
    snapshot: dict,
) -> None:
    """多 Agent 的调用**必须**出现在计量表里。

    否则成本报表会漏掉它们——而多 Agent 的调用量是单 Agent 的 7 倍，
    漏掉就是**成本严重低估**。
    这正是 L-31 刚修好的问题不能在新的调用路径上重演。
    """
    _seed_pricing(db_env)

    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(_agent_payload)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        run_multi_agent_analysis(
            snapshot=snapshot,
            settings=settings,
            db=db_env,
            analysis_id="ana_test",
            meter_db=db_env,
            record_attempt=_record_to_db,
        )
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    db_env.flush()

    model_runs = list(db_env.scalars(select(ModelRun)))
    cost_records = list(db_env.scalars(select(LLMCostRecord)))

    assert len(model_runs) == 7, f"应有 7 次调用的计量记录，实际 {len(model_runs)}"
    assert len(cost_records) == 7

    # ``agent_name`` 记在 ``llm_cost_records`` 上——
    # 它必须**区分开**各个 Agent，否则成本报表
    # 「哪个 Agent 最贵」这一栏永远答不出来（L-31 的核心问题）。
    agent_names = {row.agent_name for row in cost_records}
    assert agent_names == set(SUB_AGENT_NAMES) | {MASTER_SPEC.name}

    # 子 Agent 用 LIGHT 档，Master 用 STANDARD 档
    tiers = {row.agent_name: row.tier for row in cost_records}
    assert tiers["technical"] == "TIER_LIGHT"
    assert tiers["master"] == "TIER_STANDARD"


# --------------------------------------------------------------------------
# 档位与降级（L-35）
# --------------------------------------------------------------------------


def test_requested_tier_reaches_the_master_call(
    db_env: Session,
    settings: Settings,
    snapshot: dict,
) -> None:
    """**按触发来源算出的档位必须真的传到 Master 的模型调用**（PRD 24.3）。

    此前这一层**算出了档位却没往下传**——
    ``call_agent`` 只认 ``spec.tier``，
    于是日报/复盘该用的 DEEP 档**永远用不上**，
    而报告里写的是「请求档位」，**看起来一切正常**。

    这是典型的「算对了但没用」——
    单测能测出「算对了」，集成测试才能测出「用上了」。
    """
    _seed_pricing(db_env)

    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(_agent_payload)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        run_multi_agent_analysis(
            snapshot=snapshot,
            settings=settings,
            db=db_env,
            analysis_id="ana_test",
            meter_db=db_env,
            record_attempt=_record_to_db,
            requested_tier=ModelTier.TIER_DEEP,
        )
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    db_env.flush()

    cost_records = list(db_env.scalars(select(LLMCostRecord)))
    tiers = {row.agent_name: row.tier for row in cost_records}

    assert tiers["master"] == "TIER_DEEP", "Master 必须用调用方传入的档位"
    # 子 Agent 仍是 LIGHT——它们是抽取分类类任务，不该跟着涨档位。
    assert tiers["technical"] == "TIER_LIGHT"


def test_sub_agents_keep_light_tier_even_when_master_is_deep(
    db_env: Session,
    settings: Settings,
    snapshot: dict,
) -> None:
    """**子 Agent 恒为 LIGHT**——升档位只针对综合推理那一步。

    否则一次日报会让 6 个抽取任务也用 DEEP 模型，
    成本翻好几倍而收益接近零。
    """
    _seed_pricing(db_env)

    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(_agent_payload)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        run_multi_agent_analysis(
            snapshot=snapshot,
            settings=settings,
            db=db_env,
            analysis_id="ana_test",
            meter_db=db_env,
            record_attempt=_record_to_db,
            requested_tier=ModelTier.TIER_DEEP,
        )
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    db_env.flush()

    cost_records = list(db_env.scalars(select(LLMCostRecord)))
    for row in cost_records:
        if row.agent_name != MASTER_SPEC.name:
            assert row.tier == "TIER_LIGHT", f"{row.agent_name} 不该升档位"


def test_degradation_is_surfaced_on_the_outcome(
    db_env: Session,
    snapshot: dict,
) -> None:
    """**降级必须能被调用方拿到**——PRD 24.1「降级必须留痕」。

    此前降级信息只存在于 ``AgentResult`` 里，
    而调用方读的是 ``call_info``（多 Agent 路径上**永远是空的**），
    于是降级被**静默吞掉**：读者以为拿到的是原档位的结论。

    这里让 STANDARD 档没配模型（降级到 LIGHT），
    断言 ``outcome.degraded_reason`` 非空。
    """
    settings = Settings(
        _env_file=None,
        DATABASE_URL=os.getenv("ANALYSIS_TEST_DATABASE_URL", ""),
        REDIS_URL="redis://127.0.0.1:6379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL="https://example.invalid/v1",
        LLM_CLOUD_API_KEY="test-key",
        LLM_TIER_LIGHT_MODEL="test-model",
        LLM_TIER_STANDARD_MODEL=None,  # **故意不配** -> Master 降级到 LIGHT
    )

    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(_agent_payload)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        outcome = run_multi_agent_analysis(
            snapshot=snapshot,
            settings=settings,
            db=db_env,
            analysis_id="ana_test",
            meter_db=db_env,
            record_attempt=_noop_record,
        )
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    assert outcome.succeeded
    assert outcome.degraded_reason is not None, "降级必须留痕，不能被静默吞掉"
    assert outcome.effective_tier == "TIER_LIGHT"


# --------------------------------------------------------------------------
# 计量回调
# --------------------------------------------------------------------------


def _noop_record(db: Any, **kwargs: Any) -> None:
    """不写计量表的空实现（用于只测落库的用例）。"""


def _record_to_db(db: Any, **kwargs: Any) -> None:
    """把一次尝试写进 ``model_runs`` / ``llm_cost_records``。

    复用 ``analysis_worker._record_attempt``——
    **同一套**计量逻辑，因此多 Agent 的成本天然进成本报表，
    且 ``model_runs.agent_name`` 记录的是**实际发起调用的 Agent**。
    """
    from aicrypto.services.analysis_worker import _record_attempt

    _record_attempt(db, **kwargs)
