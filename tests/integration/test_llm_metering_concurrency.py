"""并发场景下的计量正确性（PRD 24.2 / 30.4）。

**这里防的是什么**：``prompt_versions`` 的主键是
``(agent_name, prompt_version)`` 组合。若用「先查再插」来登记版本，
多个 worker 并发处理任务时会一起查到「不存在」，然后一起插入——
第二个开始就撞唯一约束，整个事务回滚，分析任务被判为
``LLM_SCHEMA_INVALID``。

**为什么单测抓不到**：竞态窗口只在真并发下出现。单线程反复调用
「先查再插」永远是对的（查到就 return），所以常规单测全绿，
问题只在生产多 worker 场景暴露。

**为什么必须真库**：唯一约束只有真 PostgreSQL 才会真正拦截。
"""

from __future__ import annotations

import datetime as dt
import os
import threading
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from aicrypto.config.settings import Settings
from aicrypto.infrastructure.database.models.analysis import (
    LLMCostRecord,
    ModelPricing,
    ModelRun,
    PromptVersion,
)
from aicrypto.services import analysis_worker

pytestmark = pytest.mark.integration


def _success_payload() -> dict:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": '{"summary":"并发测试","market_bias":"NEUTRAL","time_horizon":"4h"}'
                },
            }
        ],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
    }


@pytest.fixture
def concurrent_settings() -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_URL=os.getenv("ANALYSIS_TEST_DATABASE_URL", ""),
        REDIS_URL="redis://127.0.0.1:6379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL="https://example.invalid/v1",
        LLM_CLOUD_API_KEY="test-key",
        LLM_TIER_STANDARD_MODEL="concurrent-meter-model",
    )


def test_prompt_version_registration_is_atomic_under_concurrency(
    concurrent_settings: Settings,
) -> None:
    """并发登记同一 Prompt 版本不能撞唯一约束。

    这是 TOCTOU 回归测试：修复前用「先查再插」，并发下第二个
    插入者会抛 ``IntegrityError``；修复后走 ``ON CONFLICT DO NOTHING``，
    所有人都能安全通过。
    """
    url = os.getenv("ANALYSIS_TEST_DATABASE_URL", "")
    engine = create_engine(url)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_payload(), request=request)

    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        # 所有线程在同一时刻冲进「先查再插」的窗口，最大化竞态概率
        barrier.wait()
        try:
            httpx.Client = patched_client  # type: ignore[assignment]
            with Session(engine) as db:
                from aicrypto.config.prompts import load_template

                template = load_template("master", "analysis")
                analysis_worker.register_prompt_version(
                    db, template=template, change_note="并发注册测试"
                )
                db.commit()
        except BaseException as exc:
            errors.append(exc)
        finally:
            httpx.Client = original_client  # type: ignore[assignment]

    try:
        with Session(engine) as setup:
            setup.execute(text("DELETE FROM prompt_versions WHERE agent_name = 'master'"))
            setup.commit()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert not errors, f"并发登记不应抛异常，实际：{errors[0]!r}"

        with Session(engine) as db:
            rows = list(
                db.scalars(select(PromptVersion).where(PromptVersion.agent_name == "master"))
            )
            assert len(rows) == 1, f"同一版本只应有一行，实际 {len(rows)} 行"
    finally:
        with Session(engine) as cleanup:
            cleanup.execute(text("DELETE FROM prompt_versions WHERE agent_name = 'master'"))
            cleanup.commit()
        engine.dispose()


def test_concurrent_measurement_is_not_duplicated_or_lost(
    concurrent_settings: Settings,
) -> None:
    """多个 worker 并发跑计量，``model_runs`` / ``llm_cost_records`` 不重不漏。"""
    url = os.getenv("ANALYSIS_TEST_DATABASE_URL", "")
    engine = create_engine(url)
    model_id = "concurrent-meter-model"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_payload(), request=request)

    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        try:
            with Session(engine) as db:
                # 单价只插一次：并发插同一行本身就会撞主键，
                # 那不是被测行为。用原子 upsert 让这一步本身并发安全。
                from sqlalchemy.dialects.postgresql import insert as pg_insert

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
                    .on_conflict_do_nothing(index_elements=["model_id", "pricing_version"])
                )
                db.commit()

            with Session(engine) as db:
                analysis_worker.generate_report({"indicators": {}}, concurrent_settings, db=db)
                db.commit()
        except BaseException as exc:
            errors.append(exc)

    try:
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert not errors, f"并发计量不应抛异常，实际：{errors[0]!r}"

        with Session(engine) as db:
            runs = list(db.scalars(select(ModelRun).where(ModelRun.model_id == model_id)))
            costs = list(
                db.scalars(select(LLMCostRecord).where(LLMCostRecord.model_id == model_id))
            )
            assert len(runs) == 4, f"应有 4 行 model_runs，实际 {len(runs)}"
            assert len(costs) == 4, f"应有 4 行 llm_cost_records，实际 {len(costs)}"

            run_ids = [r.model_run_id for r in runs]
            assert len(set(run_ids)) == 4, "model_run_id 必须互不相同"
            cost_run_ids = [c.model_run_id for c in costs]
            assert set(cost_run_ids) == set(run_ids), "成本记录必须与 model_run 一一对应"

            for cost in costs:
                assert cost.input_tokens == 1000
                assert cost.output_tokens == 200
                # 1000/1e6*1 + 200/1e6*2 = 0.0014
                assert cost.estimated_cost_usd == Decimal("0.0014000000")
    finally:
        httpx.Client = original_client  # type: ignore[assignment]
        with Session(engine) as cleanup:
            cleanup.execute(
                text("DELETE FROM llm_cost_records WHERE model_id = :m"), {"m": model_id}
            )
            cleanup.execute(text("DELETE FROM model_runs WHERE model_id = :m"), {"m": model_id})
            cleanup.execute(text("DELETE FROM prompt_versions WHERE agent_name = 'master'"))
            cleanup.commit()
        engine.dispose()
