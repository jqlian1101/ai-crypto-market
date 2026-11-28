"""LLM 重试、schema 修复、输入护栏的真库验证（PRD 24.2 / 32.4）。

**为什么必须真库**：这三项的核心断言都涉及**多次 HTTP 尝试**，
每一次尝试都要写一行 ``model_runs``、每一次都要写一行 ``llm_cost_records``，
且 ``retry_count`` 必须如实反映重试了几次。这些只有真库能验。

**这里锁死的核心不变量**：

1. **每次实际发出的请求都记账**——包括被重试掉的那些失败尝试。
   漏记会让成本报表**系统性低估**（失败尝试同样消耗 token）。
2. **不重试不该重试的**：4xx 重试只是白烧钱。
3. **schema 修复只回灌 output + error**，不重传原始快照上下文。
4. **输入超限在发请求前就拦下来**——不能等模型回包才知道，
   那时钱已经花了。
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

from aicrypto.common.errors import AICryptoError, ErrorCode
from aicrypto.config.settings import Settings
from aicrypto.infrastructure.database.models.analysis import (
    AgentOutput,
    AgentRun,
    LLMCostRecord,
    ModelPricing,
    ModelRun,
)
from aicrypto.services import analysis_worker

pytestmark = pytest.mark.integration


def _success_payload(summary: str = "重试验证") -> dict:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "summary": summary,
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


def _bad_schema_payload() -> dict:
    """JSON 合法但不符合 Schema（缺必填字段）。"""
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {"summary": "只有摘要"},
                        ensure_ascii=False,
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 800, "completion_tokens": 50},
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
def settings(db_env: Session) -> Settings:
    """依赖 ``db_env`` 是为了**保证先触发 skip**。

    若直接用 ``os.getenv(..., "")`` 的空串兜底，
    未配置测试库时会抛 ``ValidationError``（ERROR）而不是跳过（SKIPPED）。
    """
    return Settings(
        _env_file=None,
        DATABASE_URL=os.getenv("ANALYSIS_TEST_DATABASE_URL", ""),
        REDIS_URL="redis://127.0.0.1:16379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL="https://example.invalid/v1",
        LLM_CLOUD_API_KEY="test-key",
        LLM_TIER_STANDARD_MODEL="test-model",
    )


def _install_transport(payload: dict, *, status_code: int = 200):
    """装一个 mock transport，返回原始 client 以便还原。"""
    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(status_code, json=payload, request=request)
        )
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    return original_client


def _install_sequence(payloads: list[tuple[dict, int]]):
    """装一个按顺序返回不同响应的 transport。

    ``payloads`` 是 ``(payload, status_code)`` 的列表，第 n 次调用返回第 n 项。
    超出列表长度时重复最后一项。
    """
    original_client = httpx.Client
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        payload, status_code = payloads[index]
        return httpx.Response(status_code, json=payload, request=request)

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    return original_client, calls


def test_retryable_5xx_is_retried_and_each_attempt_is_recorded(
    db_env: Session, settings: Settings
) -> None:
    """5xx 是可重试错误；每次尝试都要写一行 model_runs + 一条成本记录。"""
    settings.LLM_MAX_RETRIES = 2
    settings.LLM_SCHEMA_REPAIR_ATTEMPTS = 0
    original, calls = _install_sequence([({"error": "boom"}, 503)])

    try:
        with pytest.raises(AICryptoError) as excinfo:
            analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original  # type: ignore[assignment]

    assert excinfo.value.code == ErrorCode.LLM_UNAVAILABLE
    # 1 次首发 + 2 次重试 = 3 次尝试
    assert calls["n"] == 3

    runs = list(db_env.scalars(select(ModelRun).order_by(ModelRun.created_at)))
    assert len(runs) == 3, "每一次尝试都必须记账"
    assert [r.retry_count for r in runs] == [0, 1, 2]
    assert all(r.status == "FAILED" for r in runs)

    costs = list(db_env.scalars(select(LLMCostRecord)))
    assert len(costs) == 3, "每一次尝试都要有成本记录"
    # 失败尝试的 token 为 0（响应里没有 usage），但**行必须在**
    assert all(c.estimated_cost_usd == Decimal(0) for c in costs)


def test_non_retryable_4xx_is_not_retried(db_env: Session, settings: Settings) -> None:
    """4xx 是「请求本身有问题」，重试只是白烧钱——必须只调用一次。"""
    settings.LLM_MAX_RETRIES = 2
    settings.LLM_SCHEMA_REPAIR_ATTEMPTS = 0
    original, calls = _install_sequence([({"error": "bad request"}, 400)])

    try:
        with pytest.raises(AICryptoError) as excinfo:
            analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original  # type: ignore[assignment]

    assert excinfo.value.code == ErrorCode.LLM_CONTENT_REJECTED
    assert calls["n"] == 1, "4xx 不应重试"

    runs = list(db_env.scalars(select(ModelRun)))
    assert len(runs) == 1


def test_429_is_retried_and_recorded_as_rate_limited(db_env: Session, settings: Settings) -> None:
    """429（限流）可重试，且状态必须记为 RATE_LIMITED（在 CHECK 枚举内）。"""
    settings.LLM_MAX_RETRIES = 1
    settings.LLM_SCHEMA_REPAIR_ATTEMPTS = 0
    original, calls = _install_sequence([({"error": "rate limited"}, 429)])

    try:
        with pytest.raises(AICryptoError) as excinfo:
            analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original  # type: ignore[assignment]

    assert excinfo.value.code == ErrorCode.LLM_RATE_LIMITED
    assert calls["n"] == 2

    runs = list(db_env.scalars(select(ModelRun).order_by(ModelRun.created_at)))
    assert len(runs) == 2
    assert all(r.status == "RATE_LIMITED" for r in runs)


def test_retry_succeeds_after_transient_failure(db_env: Session, settings: Settings) -> None:
    """首次 503、重试成功：应有 2 行 model_runs，成功的 retry_count=1。"""
    settings.LLM_MAX_RETRIES = 2
    settings.LLM_SCHEMA_REPAIR_ATTEMPTS = 0
    original, calls = _install_sequence([({"error": "boom"}, 503), (_success_payload(), 200)])

    try:
        content, model_id, prompt_version = analysis_worker.generate_report(
            {"indicators": {}}, settings, db=db_env
        )
    finally:
        httpx.Client = original  # type: ignore[assignment]

    assert calls["n"] == 2
    assert content.summary == "重试验证"
    assert model_id == "test-model"
    assert prompt_version == "v1"

    runs = list(db_env.scalars(select(ModelRun).order_by(ModelRun.created_at)))
    assert len(runs) == 2
    assert runs[0].status == "FAILED"
    assert runs[0].retry_count == 0
    assert runs[1].status == "SUCCESS"
    assert runs[1].retry_count == 1, "成功的那次必须记录它前面重试了 1 次"


def test_schema_repair_is_attempted_and_succeeds(db_env: Session, settings: Settings) -> None:
    """schema 失败后回灌 output+error 修一次；修好则整体成功。"""
    settings.LLM_MAX_RETRIES = 0
    settings.LLM_SCHEMA_REPAIR_ATTEMPTS = 1
    original, calls = _install_sequence(
        [(_bad_schema_payload(), 200), (_success_payload("修复后"), 200)]
    )

    try:
        content, _, _ = analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original  # type: ignore[assignment]

    assert calls["n"] == 2, "应该重发一次请求尝试修复"
    assert content.summary == "修复后"

    runs = list(db_env.scalars(select(ModelRun).order_by(ModelRun.created_at)))
    assert len(runs) == 2
    assert runs[0].status == "SCHEMA_INVALID"
    assert runs[1].status == "SUCCESS"


def test_schema_repair_does_not_resend_the_snapshot(db_env: Session, settings: Settings) -> None:
    """**修复请求不得重传原始快照上下文**（PRD v2.3 / 成本优化 O12）。

    重传快照会让每次 schema 失败的成本翻倍以上，且模型可能
    「重新分析一遍」给出不同结论，破坏可复现性。
    """
    settings.LLM_MAX_RETRIES = 0
    settings.LLM_SCHEMA_REPAIR_ATTEMPTS = 1
    original_client = httpx.Client
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        payload = _bad_schema_payload() if len(captured) == 1 else _success_payload("修好了")
        return httpx.Response(200, json=payload, request=request)

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    try:
        analysis_worker.generate_report(
            {"indicators": {"marker": "SNAPSHOT_UNIQUE_MARKER"}},
            settings,
            db=db_env,
        )
    finally:
        httpx.Client = original_client  # type: ignore[assignment]

    assert len(captured) == 2
    first_body = json.dumps(captured[0], ensure_ascii=False)
    second_body = json.dumps(captured[1], ensure_ascii=False)
    # requests 的 body 是 bytes；这里直接按 str 解一次，避免二次转义
    second_text = captured[1]["messages"][-1]["content"]

    # 第一次请求里有快照
    assert "SNAPSHOT_UNIQUE_MARKER" in first_body
    # 第二次（修复）请求里**必须没有**快照
    assert "SNAPSHOT_UNIQUE_MARKER" not in second_body, "修复请求不得重传原始快照"
    # 但必须带上「上一次的输出」与「错误」
    assert "只有摘要" in second_text, "修复请求必须回灌模型上一次的输出"
    assert "market_bias" in second_text, "修复请求必须带上校验错误明细"


def test_schema_repair_exhausted_raises_and_never_fakes_output(
    db_env: Session, settings: Settings
) -> None:
    """修复也失败时必须抛错，**绝不允许伪造输出**（PRD 32.4）。"""
    settings.LLM_MAX_RETRIES = 0
    settings.LLM_SCHEMA_REPAIR_ATTEMPTS = 1
    original, calls = _install_sequence([(_bad_schema_payload(), 200)])

    try:
        with pytest.raises(AICryptoError) as excinfo:
            analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original  # type: ignore[assignment]

    assert excinfo.value.code == ErrorCode.LLM_SCHEMA_INVALID
    # 首发 + 1 次修复 = 2 次尝试
    assert calls["n"] == 2

    runs = list(db_env.scalars(select(ModelRun).order_by(ModelRun.created_at)))
    assert len(runs) == 2
    assert all(r.status == "SCHEMA_INVALID" for r in runs)


def test_schema_repair_disabled_means_single_attempt(db_env: Session, settings: Settings) -> None:
    """``LLM_SCHEMA_REPAIR_ATTEMPTS=0`` 时不重发请求。"""
    settings.LLM_MAX_RETRIES = 0
    settings.LLM_SCHEMA_REPAIR_ATTEMPTS = 0
    original, calls = _install_sequence([(_bad_schema_payload(), 200)])

    try:
        with pytest.raises(AICryptoError):
            analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original  # type: ignore[assignment]

    assert calls["n"] == 1


def test_input_token_guard_blocks_before_sending(db_env: Session, settings: Settings) -> None:
    """输入超限必须在**发请求前**拦下来。

    输入阶梯计价（>100K 时单价 ×5），超限不是性能问题而是成本问题，
    等模型回包才知道就晚了——钱已经花了。
    """
    settings.LLM_MAX_INPUT_TOKENS = 1024
    settings.LLM_MAX_RETRIES = 0
    settings.LLM_SCHEMA_REPAIR_ATTEMPTS = 0
    original, calls = _install_sequence([(_success_payload(), 200)])

    try:
        with pytest.raises(AICryptoError) as excinfo:
            analysis_worker.generate_report({"indicators": {"x": "y" * 20000}}, settings, db=db_env)
    finally:
        httpx.Client = original  # type: ignore[assignment]

    assert excinfo.value.code == ErrorCode.LLM_BUDGET_EXCEEDED
    assert calls["n"] == 0, "超限的请求根本不该发出去"
    assert excinfo.value.details["stage"] == "input_token_limit"

    # 被拦下的请求不产生任何调用记录
    assert list(db_env.scalars(select(ModelRun))) == []


def test_input_token_guard_allows_normal_input(db_env: Session, settings: Settings) -> None:
    """正常大小的输入不该被拦。"""
    settings.LLM_MAX_INPUT_TOKENS = 24000
    settings.LLM_MAX_RETRIES = 0
    settings.LLM_SCHEMA_REPAIR_ATTEMPTS = 0
    original, calls = _install_sequence([(_success_payload(), 200)])

    try:
        analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original  # type: ignore[assignment]

    assert calls["n"] == 1


def test_retry_respects_total_budget_deadline(db_env: Session, settings: Settings) -> None:
    """总预算不够覆盖退避时，必须**放弃重试**而不是继续耗下去。"""
    settings.LLM_MAX_RETRIES = 3
    settings.LLM_SCHEMA_REPAIR_ATTEMPTS = 0
    original, calls = _install_sequence([({"error": "boom"}, 503)])

    try:
        with pytest.raises(AICryptoError):
            analysis_worker.generate_report(
                {"indicators": {}},
                settings,
                db=db_env,
                deadline=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1),
            )
    finally:
        httpx.Client = original  # type: ignore[assignment]

    assert calls["n"] == 1, "截止时间已过，不应重试"


def test_priced_cost_aggregates_across_retries(db_env: Session, settings: Settings) -> None:
    """多次尝试的费用要各自定价，总和等于各次之和。"""
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

    settings.LLM_MAX_RETRIES = 1
    settings.LLM_SCHEMA_REPAIR_ATTEMPTS = 0
    original, calls = _install_sequence([({"error": "boom"}, 503), (_success_payload(), 200)])

    try:
        analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
    finally:
        httpx.Client = original  # type: ignore[assignment]

    assert calls["n"] == 2

    costs = list(db_env.scalars(select(LLMCostRecord).order_by(LLMCostRecord.created_at)))
    assert len(costs) == 2
    # 失败的第一次没有 usage -> token 0 -> 费用 0，但 priced 标记为 True
    # （单价是已知的，只是这次调用的用量未知）
    assert costs[0].estimated_cost_usd == Decimal(0)
    assert costs[0].price_snapshot["priced"] is True
    # 成功那次：1200 in * 3.0/M + 340 out * 15.0/M = 0.0036 + 0.0051 = 0.0087
    assert costs[1].estimated_cost_usd == Decimal("0.0087000000")


class TestMissingAgentMarking:
    """修复仍失败时标记 Agent 缺失，禁止伪造输出（PRD 32.4）。"""

    def test_record_missing_agent_writes_failed_degraded_run(
        self, db_env: Session, settings: Settings
    ) -> None:
        agent_run_id = analysis_worker.record_missing_agent(
            db_env,
            run_id="run_x",
            analysis_id="an_x",
            symbol="BTCUSDT",
            market_type="SPOT",
            snapshot_id="snap_x",
            as_of=dt.datetime.now(dt.UTC),
            reason="Schema 修复仍失败",
            error={"code": "LLM_SCHEMA_INVALID"},
        )
        db_env.flush()

        runs = list(db_env.scalars(select(AgentRun)))
        assert len(runs) == 1
        row = runs[0]
        assert row.run_id == agent_run_id
        assert row.agent_name == "master"
        assert row.status == "FAILED"
        assert row.degraded is True
        assert "Schema 修复仍失败" in (row.degraded_reason or "")
        assert row.error["code"] == "LLM_SCHEMA_INVALID"

    def test_missing_agent_writes_no_output_row(self, db_env: Session, settings: Settings) -> None:
        """**没有输出就没有输出行**——不能塞占位空对象假装有输出。"""
        analysis_worker.record_missing_agent(
            db_env,
            run_id="run_y",
            analysis_id="an_y",
            symbol="BTCUSDT",
            market_type="SPOT",
            snapshot_id="snap_y",
            as_of=dt.datetime.now(dt.UTC),
            reason="修复仍失败",
        )
        db_env.flush()

        assert list(db_env.scalars(select(AgentOutput))) == []
