"""LLM 指标的接通验证（TODO「写入 llm_daily_cost_usd 指标数据」）。

**这些指标此前定义了却无人写入**（与 L-16 同型的「声明未消费」）。
本文件锁死「指标真的被写了」这件事——否则指标只是躺在
``metrics.py`` 里的定义，线上永远读到 0。

**指标与数据库是两条独立的观测通道**：
- 数据库：审计用的权威记录（可回溯、可对账）
- 指标：运维用的实时信号（可告警、可画图）

缺了指标，线上费用突增只能等月底对账才发现。
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from aicrypto.infrastructure import metrics
from aicrypto.infrastructure.database.models.analysis import ModelPricing
from aicrypto.services import analysis_worker

pytestmark = pytest.mark.integration


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


@pytest.fixture
def settings(db_env: Session):
    """依赖 ``db_env`` 是为了**保证先触发 skip**。

    若直接用 ``os.getenv(..., "")`` 的空串兜底，
    未配置测试库时会抛 ``ValidationError``（ERROR）而不是跳过（SKIPPED）。
    """
    from aicrypto.config.settings import Settings

    return Settings(
        _env_file=None,
        DATABASE_URL=os.getenv("ANALYSIS_TEST_DATABASE_URL", ""),
        REDIS_URL="redis://127.0.0.1:16379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL="https://example.invalid/v1",
        LLM_CLOUD_API_KEY="k",
        LLM_TIER_STANDARD_MODEL="metric-model",
    )


def _counter_value(counter, **labels) -> float:
    """读取某个 label 组合的 counter 当前值。"""
    return counter.labels(**labels)._value.get()


def test_model_run_publishes_request_and_token_metrics(db_env: Session, settings) -> None:
    """一次成功调用必须让请求数、token、费用指标都动起来。"""
    pricing = ModelPricing(
        model_id="metric-model",
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

    before_requests = _counter_value(
        metrics.llm_requests_total,
        model_id="metric-model",
        tier="TIER_STANDARD",
        agent="master",
        status="SUCCESS",
    )
    before_in = _counter_value(metrics.llm_tokens_total, model_id="metric-model", direction="in")
    before_out = _counter_value(metrics.llm_tokens_total, model_id="metric-model", direction="out")

    analysis_worker.record_model_run(
        db_env,
        provider="cloud",
        model_id="metric-model",
        prompt_version="v1",
        status="SUCCESS",
        input_tokens=1000,
        output_tokens=500,
        latency_ms=1234,
        retry_count=0,
        error=None,
        agent_name="master",
        tier="TIER_STANDARD",
        pricing=analysis_worker.resolve_pricing(
            db_env, model_id="metric-model", as_of=dt.datetime.now(dt.UTC)
        ),
    )
    db_env.flush()

    assert (
        _counter_value(
            metrics.llm_requests_total,
            model_id="metric-model",
            tier="TIER_STANDARD",
            agent="master",
            status="SUCCESS",
        )
        == before_requests + 1
    )
    assert (
        _counter_value(metrics.llm_tokens_total, model_id="metric-model", direction="in")
        == before_in + 1000
    )
    assert (
        _counter_value(metrics.llm_tokens_total, model_id="metric-model", direction="out")
        == before_out + 500
    )


def test_schema_invalid_publishes_schema_invalid_counter(db_env: Session, settings) -> None:
    """Schema 失败必须让 llm_schema_invalid_total 动起来（可用于告警）。"""
    before = _counter_value(metrics.llm_schema_invalid_total, agent="master")

    analysis_worker.record_model_run(
        db_env,
        provider="cloud",
        model_id="metric-model",
        prompt_version="v1",
        status="SCHEMA_INVALID",
        input_tokens=10,
        output_tokens=0,
        latency_ms=100,
        retry_count=1,
        error={"stage": "output_validation"},
        agent_name="master",
        tier="TIER_STANDARD",
        pricing=None,
    )
    db_env.flush()

    assert _counter_value(metrics.llm_schema_invalid_total, agent="master") == before + 1


def test_daily_cost_gauge_is_set(db_env: Session, settings) -> None:
    """llm_daily_cost_usd 必须被写入（TODO 明确要求「写入指标数据」）。"""
    pricing = ModelPricing(
        model_id="metric-model",
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

    analysis_worker.record_model_run(
        db_env,
        provider="cloud",
        model_id="metric-model",
        prompt_version="v1",
        status="SUCCESS",
        input_tokens=1_000_000,
        output_tokens=0,
        latency_ms=100,
        retry_count=0,
        error=None,
        agent_name="master",
        tier="TIER_STANDARD",
        pricing=analysis_worker.resolve_pricing(
            db_env, model_id="metric-model", as_of=dt.datetime.now(dt.UTC)
        ),
    )
    db_env.flush()

    # 1000000 input tokens * $3.0/M = $3.00
    samples = list(next(iter(metrics.llm_daily_cost_usd.collect())).samples)
    assert samples
    assert samples[0].value == pytest.approx(3.0)


def test_metrics_failure_does_not_break_the_call(db_env: Session, settings) -> None:
    """**观测通道不得影响业务通道**：指标写失败也不能让分析失败。"""
    import aicrypto.services.analysis_worker as worker

    original = worker.metrics.llm_requests_total

    class Broken:
        def labels(self, **kwargs):
            raise RuntimeError("指标系统挂了")

    worker.metrics.llm_requests_total = Broken()
    try:
        run_id = worker.record_model_run(
            db_env,
            provider="cloud",
            model_id="metric-model",
            prompt_version="v1",
            status="SUCCESS",
            input_tokens=10,
            output_tokens=5,
            latency_ms=100,
            retry_count=0,
            error=None,
            agent_name="master",
            tier="TIER_STANDARD",
            pricing=None,
        )
    finally:
        worker.metrics.llm_requests_total = original

    db_env.flush()
    assert run_id  # 调用没被指标异常打断
