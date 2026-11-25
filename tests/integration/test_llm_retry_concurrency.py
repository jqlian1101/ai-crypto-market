"""真并发 + 重试的组合压测（真库）。

**为什么这个组合特别危险**：

普通并发测试（``test_llm_metering_concurrency.py``）验证的是
「4 个 worker 各自成功 1 次 -> 4 行记录」。但生产里的真实情况是
**并发 + 重试**：多个 worker 同时遇到限流（429），
各自退避重试若干次才成功。

这时最容易出错的不是「记少了」，而是：
- **重试次数被串了**——某个 worker 的第 2 次尝试被记成另一个 worker 的；
- **成本被重复计**——同一次尝试写了两次 ``llm_cost_records``；
- **孤儿记录**——``llm_cost_records`` 引用了不存在的 ``model_run_id``；
- **retry_count 不连续**——比如出现 `[0, 0, 1, 2]`（说明并发下计数错乱）。

本文件把这些不变量全部锁死。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from aicrypto.config.settings import Settings
from aicrypto.infrastructure.database.models.analysis import (
    LLMCostRecord,
    ModelPricing,
    ModelRun,
)
from aicrypto.services import analysis_worker

pytestmark = pytest.mark.integration

WORKERS = 4
RETRIES = 2
#: 每个 worker 会遇到多少次 429 才会成功
FAILURES_BEFORE_SUCCESS = 2


def _success_payload() -> dict:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "summary": "并发重试成功",
                            "market_bias": "NEUTRAL",
                            "time_horizon": "4h",
                        },
                        ensure_ascii=False,
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
    }


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


def test_concurrent_workers_with_retries_account_exactly(
    db_env: Session,
) -> None:
    """4 个 worker 并发、每个重试 2 次才成功 -> 记账必须精确。

    **期望**：每个 worker 产生 ``1 + FAILURES_BEFORE_SUCCESS`` 次尝试，
    总计 ``WORKERS * (1 + FAILURES_BEFORE_SUCCESS)`` 行 ``model_runs``
    与同样多的 ``llm_cost_records``。
    """
    url = os.getenv("ANALYSIS_TEST_DATABASE_URL", "")
    engine = create_engine(url)
    model_id = "concurrent-retry-model"

    settings = Settings(
        _env_file=None,
        DATABASE_URL=url,
        REDIS_URL="redis://127.0.0.1:6379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL="https://example.invalid/v1",
        LLM_CLOUD_API_KEY="k",
        LLM_TIER_STANDARD_MODEL=model_id,
        LLM_MAX_RETRIES=RETRIES,
        LLM_SCHEMA_REPAIR_ATTEMPTS=0,
    )

    # 每个 worker 独立的调用计数（用 thread-local 区分）
    local = threading.local()

    def handler(request: httpx.Request) -> httpx.Response:
        n = getattr(local, "n", 0)
        local.n = n + 1
        if n < FAILURES_BEFORE_SUCCESS:
            return httpx.Response(429, json={"error": "rate limited"}, request=request)
        return httpx.Response(200, json=_success_payload(), request=request)

    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    errors: list[BaseException] = []
    barrier = threading.Barrier(WORKERS)

    def worker() -> None:
        local.n = 0
        barrier.wait()
        try:
            with Session(engine) as db:
                db.execute(
                    pg_insert(ModelPricing)
                    .values(
                        model_id=model_id,
                        pricing_version="v1",
                        provider="cloud",
                        input_usd_per_mtok=Decimal("1.0"),
                        output_usd_per_mtok=Decimal("2.0"),
                        currency="USD",
                        effective_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
                        created_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
                    )
                    .on_conflict_do_nothing(
                        index_elements=["model_id", "pricing_version"]
                    )
                )
                db.commit()

            with Session(engine) as db:
                analysis_worker.generate_report(
                    {"indicators": {}}, settings, db=db
                )
                db.commit()
        except BaseException as exc:
            errors.append(exc)

    try:
        threads = [threading.Thread(target=worker) for _ in range(WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        assert not errors, f"并发重试不应抛异常，实际：{errors[0]!r}"

        expected = WORKERS * (1 + FAILURES_BEFORE_SUCCESS)
        with Session(engine) as db:
            runs = list(
                db.scalars(
                    select(ModelRun)
                    .where(ModelRun.model_id == model_id)
                    .order_by(ModelRun.created_at)
                )
            )
            costs = list(
                db.scalars(
                    select(LLMCostRecord)
                    .where(LLMCostRecord.model_id == model_id)
                    .order_by(LLMCostRecord.created_at)
                )
            )

            assert len(runs) == expected, (
                f"应有 {expected} 行 model_runs（4 worker x 3 次尝试），实际 {len(runs)}"
            )
            assert len(costs) == expected, (
                f"应有 {expected} 行 llm_cost_records，实际 {len(costs)}"
            )

            # ---- 一一对应，无孤儿 ----
            run_ids = {r.model_run_id for r in runs}
            assert len(run_ids) == expected, "model_run_id 必须互不相同"
            cost_run_ids = [c.model_run_id for c in costs]
            assert set(cost_run_ids) == run_ids, "成本记录必须与 model_run 一一对应"
            assert len(set(cost_run_ids)) == expected, "**不得有重复的成本记录**"

            # ---- 状态分布正确 ----
            statuses = [r.status for r in runs]
            assert statuses.count("RATE_LIMITED") == WORKERS * FAILURES_BEFORE_SUCCESS, (
                f"应有 {WORKERS * FAILURES_BEFORE_SUCCESS} 次限流失败，实际 "
                f"{statuses.count('RATE_LIMITED')}"
            )
            assert statuses.count("SUCCESS") == WORKERS, (
                f"应有 {WORKERS} 次成功，实际 {statuses.count('SUCCESS')}"
            )

            # ---- retry_count 必须是从 0 开始的连续序列 ----
            # 每个 worker 的 3 次尝试是 0,1,2。并发交错后全局排序可能乱，
            # 但**每一行的 retry_count 必须在 [0, RETRIES] 内**，
            # 且每个 worker 恰好有且仅有一行 retry_count=0/1/2。
            retry_counts = sorted(r.retry_count for r in runs)
            assert retry_counts == sorted(
                [0, 1, 2] * WORKERS
            ), f"retry_count 分布错误：{retry_counts}"
            assert all(0 <= r.retry_count <= RETRIES for r in runs), (
                "retry_count 不得超出配置的 LLM_MAX_RETRIES"
            )

            # ---- 成功的那些，token 与费用必须真实 ----
            for run in runs:
                if run.status == "SUCCESS":
                    assert run.input_tokens == 1000
                    assert run.output_tokens == 200
                    assert run.retry_count == FAILURES_BEFORE_SUCCESS, (
                        "成功那次必须如实记录它前面重试了几次"
                    )

            # ---- 每次尝试都有成本记录（失败的也要）----
            for cost in costs:
                assert cost.tier == "TIER_STANDARD"
                # 失败尝试没有 usage -> token 0；成功的那次有真实 token
                if cost.input_tokens == 0:
                    assert cost.estimated_cost_usd == Decimal(0)
                else:
                    # 1000 in * 1.0/M + 200 out * 2.0/M = 0.001 + 0.0004 = 0.0014
                    assert cost.estimated_cost_usd == Decimal("0.0014000000")

            # 费用总和必须等于各次之和
            total = sum((c.estimated_cost_usd for c in costs), Decimal(0))
            assert total == Decimal("0.0056000000"), f"总费用错误：{total}"
    finally:
        httpx.Client = original_client  # type: ignore[assignment]
        with Session(engine) as clean:
            clean.execute(
                LLMCostRecord.__table__.delete().where(
                    LLMCostRecord.model_id == model_id
                )
            )
            clean.execute(
                ModelRun.__table__.delete().where(ModelRun.model_id == model_id)
            )
            clean.execute(
                ModelPricing.__table__.delete().where(ModelPricing.model_id == model_id)
            )
            clean.commit()
        engine.dispose()


def test_concurrent_retry_does_not_duplicate_prompt_versions(
    db_env: Session,
) -> None:
    """并发重试时，Prompt 版本登记**不得重复插入**。

    重试会让同一个逻辑调用发出多次请求，每次成功都会走到
    ``register_prompt_version``。若那里是「先查再插」，
    并发下就会撞唯一约束（L-17 的同型问题在重试路径上的复现）。
    """
    url = os.getenv("ANALYSIS_TEST_DATABASE_URL", "")
    engine = create_engine(url)
    model_id = "concurrent-prompt-model"

    settings = Settings(
        _env_file=None,
        DATABASE_URL=url,
        REDIS_URL="redis://127.0.0.1:6379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL="https://example.invalid/v1",
        LLM_CLOUD_API_KEY="k",
        LLM_TIER_STANDARD_MODEL=model_id,
        LLM_MAX_RETRIES=1,
        LLM_SCHEMA_REPAIR_ATTEMPTS=0,
    )

    local = threading.local()

    def handler(request: httpx.Request) -> httpx.Response:
        n = getattr(local, "n", 0)
        local.n = n + 1
        if n == 0:
            return httpx.Response(503, json={"error": "boom"}, request=request)
        return httpx.Response(200, json=_success_payload(), request=request)

    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    errors: list[BaseException] = []
    barrier = threading.Barrier(WORKERS)

    def worker() -> None:
        local.n = 0
        barrier.wait()
        try:
            with Session(engine) as db:
                analysis_worker.generate_report({"indicators": {}}, settings, db=db)
                db.commit()
        except BaseException as exc:
            errors.append(exc)

    try:
        threads = [threading.Thread(target=worker) for _ in range(WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        assert not errors, f"并发重试不应抛异常，实际：{errors[0]!r}"

        from aicrypto.infrastructure.database.models.analysis import PromptVersion

        with Session(engine) as db:
            versions = list(db.scalars(select(PromptVersion)))
            # 主键是 (agent_name, template_name, prompt_version) 三元组，
            # 因此 system 与 user 两个模板**各自一行**，共 2 行。
            # （旧二元组主键下 user 会被静默丢弃，只剩 1 行 —— L-22）
            names = {v.template_name for v in versions}
            assert names == {"analysis", "analysis.user"}, (
                f"两个模板都必须登记，实际 {names}："
                f"{[(v.agent_name, v.template_name, v.prompt_version) for v in versions]}"
            )
            assert len(versions) == 2, (
                f"应恰好 2 行（两个模板各一行），实际 {len(versions)}"
            )
            # 幂等：4 个 worker x 2 次登记 = 8 次插入，但只有 2 行
            keys = {
                (v.agent_name, v.template_name, v.prompt_version) for v in versions
            }
            assert len(keys) == 2, "同一模板版本只应有一行"
    finally:
        httpx.Client = original_client  # type: ignore[assignment]
        with Session(engine) as clean:
            clean.execute(
                ModelRun.__table__.delete().where(ModelRun.model_id == model_id)
            )
            clean.execute(
                LLMCostRecord.__table__.delete().where(
                    LLMCostRecord.model_id == model_id
                )
            )
            clean.commit()
        engine.dispose()
