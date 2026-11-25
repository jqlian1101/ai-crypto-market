"""LLM 计量接线的真库验证（PRD 24.2 / 30.4）。

**背景**��``model_runs`` / ``llm_cost_records`` / ``prompt_versions``
三张表早已存在，但**没有任何写入方**——成本分析无从谈起。
这是与 L-07 同类的静默失效：表建了、没人写、看起来一切正常。

这些测试用 **mock transport** 走完整链路，断言三件事：

1. 每次模型调用都写一行 ``model_runs``（含 token 与延迟）；
2. 每次模型调用都写一行 ``llm_cost_records``（含估算费用）；
3. ``prompt_versions`` 被登记，且 ``template_hash`` 与磁盘文件一致；
   报告的 ``versions`` 字段不再为空。

**为什么必须真库**：单元测试用假 Session 断言「调用了 add()」，
但无法验证**事务边界**（成本记录与报告是否同生共死）、
**CHECK 约束**（status 是否在允许枚举内）与**唯一约束**
（prompt_versions 是否会重复插入）。这些只有真库能暴露。
"""

from __future__ import annotations

import datetime as dt
import json
import os
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aicrypto.common.errors import AICryptoError
from aicrypto.config.prompts import load_template
from aicrypto.config.settings import Settings
from aicrypto.infrastructure.database.models.analysis import (
    LLMCostRecord,
    ModelPricing,
    ModelRun,
    PromptVersion,
)
from aicrypto.services import analysis_worker

pytestmark = pytest.mark.integration


def _mock_transport(payload: dict, *, status_code: int = 200) -> httpx.MockTransport:
    """构造一个返回固定 payload 的 mock transport。"""
    return httpx.MockTransport(
        lambda request: httpx.Response(status_code, json=payload, request=request)
    )


def _success_payload() -> dict:
    """一份合法的报告响应。"""
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "summary": "综合判断",
                            "market_bias": "NEUTRAL",
                            "time_horizon": "4h",
                        },
                        ensure_ascii=False,
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 340},
    }


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
def settings() -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_URL=os.getenv("ANALYSIS_TEST_DATABASE_URL", ""),
        REDIS_URL="redis://127.0.0.1:6379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL="https://example.invalid/v1",
        LLM_CLOUD_API_KEY="test-key",
        LLM_TIER_STANDARD_MODEL="test-model",
    )


def test_model_run_and_cost_are_recorded(db_env: Session, settings: Settings) -> None:
    """一次成功调用必须写一行 model_runs + 一行 llm_cost_records。"""
    pricing = ModelPricing(
        model_id="test-model",
        pricing_version="v1",
        provider="cloud",
        input_usd_per_mtok=Decimal("3.0"),
        output_usd_per_mtok=Decimal("15.0"),
        currency="USD",
        effective_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        created_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
    )
    db_env.add(pricing)
    db_env.flush()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_payload(), request=request)

    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        content, model_id, prompt_version = analysis_worker.generate_report(
            {"indicators": {}}, settings, db=db_env
        )
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    assert content.summary == "综合判断"
    assert model_id == "test-model"
    assert prompt_version == "v1"

    runs = list(db_env.scalars(select(ModelRun)))
    assert len(runs) == 1
    assert runs[0].status == "SUCCESS"
    assert runs[0].input_tokens == 1200
    assert runs[0].output_tokens == 340
    assert runs[0].model_id == "test-model"
    assert runs[0].prompt_version == "v1"
    assert runs[0].retry_count == 0

    costs = list(db_env.scalars(select(LLMCostRecord)))
    assert len(costs) == 1
    assert costs[0].input_tokens == 1200
    assert costs[0].output_tokens == 340
    assert costs[0].model_run_id == runs[0].model_run_id
    # 1200/1e6*3.0 + 340/1e6*15.0 = 0.0036 + 0.0051 = 0.0087
    assert costs[0].estimated_cost_usd == Decimal("0.0087")
    assert costs[0].price_snapshot["pricing_version"] == "v1"
    assert costs[0].price_snapshot["priced"] is True
    assert costs[0].tier == "TIER_STANDARD"
    assert costs[0].agent_name == "master"


def test_cost_without_pricing_is_flagged_not_zero(db_env: Session, settings: Settings) -> None:
    """单价缺失时成本记 0 但**显式标记未定价**——不能让人误读成「免费」。"""
    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(_success_payload())
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    costs = list(db_env.scalars(select(LLMCostRecord)))
    assert len(costs) == 1
    assert costs[0].estimated_cost_usd == Decimal(0)
    # 关键：快照里必须有 priced=False 标记
    assert costs[0].price_snapshot["priced"] is False
    assert "pricing_version" not in costs[0].price_snapshot


def test_failed_call_still_recorded(db_env: Session, settings: Settings) -> None:
    """失败调用也消耗 token，必须记入 model_runs —— 否则成本系统性低估。

    这里用 ``LLM_MAX_RETRIES=0`` 关掉重试：500 是**可重试**错误，
    默认配置下会被重试 ``LLM_MAX_RETRIES`` 次，每次尝试都要记账。
    重试语义由 ``test_retry_*`` 系列单独覆盖。
    """
    settings.LLM_MAX_RETRIES = 0
    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport({"error": "boom"}, status_code=500)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        with pytest.raises(AICryptoError):
            analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    runs = list(db_env.scalars(select(ModelRun)))
    assert len(runs) == 1
    assert runs[0].status == "FAILED"
    assert runs[0].error["stage"] == "provider_http"

    # 失败调用同样要有一条成本记录（token 为 0，因为响应里没有 usage）
    costs = list(db_env.scalars(select(LLMCostRecord)))
    assert len(costs) == 1


def test_schema_invalid_call_recorded(db_env: Session, settings: Settings) -> None:
    """schema 校验失败必须记为 SCHEMA_INVALID（model_runs 的 CHECK 枚举内）。

    这里同时关掉重试与 schema 修复：修复会多发一次请求，
    多写一行 ``model_runs``。修复语义由 ``test_retry_*`` 系列单独覆盖。
    """
    settings.LLM_MAX_RETRIES = 0
    settings.LLM_SCHEMA_REPAIR_ATTEMPTS = 0
    payload = {
        "choices": [{"finish_reason": "stop", "message": {"content": "not-json"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
    }
    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(payload)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        with pytest.raises(AICryptoError):
            analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    runs = list(db_env.scalars(select(ModelRun)))
    assert len(runs) == 1
    assert runs[0].status == "SCHEMA_INVALID"


def test_prompt_version_registered_with_matching_hash(db_env: Session, settings: Settings) -> None:
    """prompt_versions 被登记，且哈希与磁盘文件一致。"""
    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(_success_payload())
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    versions = list(db_env.scalars(select(PromptVersion)))
    assert versions, "prompt_versions 必须被登记"
    template = load_template("master", "analysis")
    system_row = next(item for item in versions if item.template_path.endswith("analysis.v1.md"))
    assert system_row.template_hash == template.template_hash
    assert system_row.agent_name == "master"
    assert system_row.template_name == "analysis"


def test_both_prompt_templates_are_registered(db_env: Session, settings: Settings) -> None:
    """**回归（L-22）**：system 与 user 两个模板**都必须登记**。

    曾经的缺陷：``prompt_versions`` 主键是 ``(agent_name, prompt_version)``，
    而 ``analysis.v1.md`` 与 ``analysis.user.v1.md`` **都是 ``(master, v1)``**
    ——user 模板的登记被 ``ON CONFLICT DO NOTHING`` **静默丢弃**。

    后果：Prompt 版本审计不完整——user 模板的哈希从来没进过库，
    而它承载了「快照怎么渲染给模型」的全部指令。
    「改一个字的 Prompt 无法追溯是哪一版产出」（PRD 24.5）
    对 user 模板完全失效。
    """
    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(_success_payload())
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    versions = list(db_env.scalars(select(PromptVersion)))
    names = {item.template_name for item in versions}
    assert "analysis" in names, "system 模板必须登记"
    assert "analysis.user" in names, "**user 模板必须登记，不能被静默丢弃**"

    user_row = next(item for item in versions if item.template_name == "analysis.user")
    user_template = load_template("master", "analysis.user")
    assert user_row.template_hash == user_template.template_hash
    assert user_row.template_path.endswith("analysis.user.v1.md")


def test_prompt_version_registration_is_idempotent(db_env: Session, settings: Settings) -> None:
    """重复登记同一版本不能触发唯一约束冲突。"""
    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(_success_payload())
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
        db_env.flush()
        analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
        db_env.flush()
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    versions = list(db_env.scalars(select(PromptVersion)))
    # 同一 (agent, template, version) 只应有一行
    keys = {(item.agent_name, item.template_name, item.prompt_version) for item in versions}
    assert len(keys) == len(versions), "同一模板版本只应有一行"


def test_pricing_picks_latest_effective_row(db_env: Session, settings: Settings) -> None:
    """同一模型多次改价时，取 as_of 时点生效的最新一条。"""
    base = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    db_env.add(
        ModelPricing(
            model_id="test-model",
            pricing_version="v1",
            provider="cloud",
            input_usd_per_mtok=Decimal("3.0"),
            output_usd_per_mtok=Decimal("15.0"),
            currency="USD",
            effective_from=base,
            created_at=base,
        )
    )
    db_env.add(
        ModelPricing(
            model_id="test-model",
            pricing_version="v2",
            provider="cloud",
            input_usd_per_mtok=Decimal("6.0"),
            output_usd_per_mtok=Decimal("30.0"),
            currency="USD",
            effective_from=base + dt.timedelta(days=1),
            created_at=base + dt.timedelta(days=1),
        )
    )
    db_env.flush()

    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(_success_payload())
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    costs = list(db_env.scalars(select(LLMCostRecord)))
    # 「现在」晚于两条单价的生效时间，应取 v2
    assert costs[0].price_snapshot["pricing_version"] == "v2"
    # 1200/1e6*6.0 + 340/1e6*30.0 = 0.0072 + 0.0102 = 0.0174
    assert costs[0].estimated_cost_usd == Decimal("0.0174")
