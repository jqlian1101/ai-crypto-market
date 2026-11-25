"""模型分层的真库验证（PRD 24.1 / 24.3）。

**核心不变量**：

1. **档位必须落库**——``model_runs.tier`` / ``llm_cost_records.tier``
   是成本报表按档位分组的唯一依据。不落库，分层就白做了。
2. **降级必须留痕**——实际生效的档位（可能低于请求的档位）要被记录，
   且报告的 ``degraded`` / ``degraded_reason`` 必须被写入。
3. **本地不可用时回退云端**，且**回退要能被看出来**。
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aicrypto.common.errors import AICryptoError
from aicrypto.config.settings import Settings
from aicrypto.domain.analysis.agent_output import ModelTier
from aicrypto.infrastructure.database.models.analysis import LLMCostRecord, ModelRun
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


def _settings(**kwargs) -> Settings:
    base = {
        "_env_file": None,
        "DATABASE_URL": os.getenv("ANALYSIS_TEST_DATABASE_URL", ""),
        "REDIS_URL": "redis://127.0.0.1:6379/0",
        "AUTH_ENABLED": False,
        "LLM_CLOUD_BASE_URL": "https://example.invalid/v1",
        "LLM_CLOUD_API_KEY": "cloud-key",
        "LLM_TIER_LIGHT_MODEL": "light-model",
        "LLM_TIER_STANDARD_MODEL": "standard-model",
        "LLM_TIER_DEEP_MODEL": "deep-model",
        "LLM_MAX_RETRIES": 0,
        "LLM_SCHEMA_REPAIR_ATTEMPTS": 0,
    }
    base.update(kwargs)
    return Settings(**base)


def _success_payload(summary: str = "分层验证") -> dict:
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
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
    }


def _install(payload: dict, *, status_code: int = 200):
    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(status_code, json=payload, request=request)
        )
        return original_client(*args, **kwargs)

    httpx.Client = patched_client  # type: ignore[assignment]
    return original_client


class TestTierAccounting:
    """档位落库。

    **注意**：``model_runs`` 表**没有** ``tier`` 列——按档位分组是
    成本报表的需求，所以档位落在 ``llm_cost_records.tier``
    （它通过 ``model_run_id`` 关联到每一次尝试）。
    """

    def test_light_tier_is_recorded(self, db_env: Session) -> None:
        """LIGHT 档必须真的落到 tier 列（此前硬编码 TIER_STANDARD）。"""
        settings = _settings()
        original = _install(_success_payload())
        try:
            analysis_worker.generate_report(
                {"indicators": {}},
                settings,
                db=db_env,
                tier=ModelTier.TIER_LIGHT,
            )
        finally:
            httpx.Client = original  # type: ignore[assignment]

        runs = list(db_env.scalars(select(ModelRun)))
        assert len(runs) == 1
        assert runs[0].model_id == "light-model"

        costs = list(db_env.scalars(select(LLMCostRecord)))
        assert len(costs) == 1
        assert costs[0].tier == "TIER_LIGHT"

    def test_deep_tier_is_recorded(self, db_env: Session) -> None:
        settings = _settings()
        original = _install(_success_payload())
        try:
            analysis_worker.generate_report(
                {"indicators": {}},
                settings,
                db=db_env,
                tier=ModelTier.TIER_DEEP,
            )
        finally:
            httpx.Client = original  # type: ignore[assignment]

        runs = list(db_env.scalars(select(ModelRun)))
        assert runs[0].model_id == "deep-model"
        costs = list(db_env.scalars(select(LLMCostRecord)))
        assert costs[0].tier == "TIER_DEEP"

    def test_default_tier_is_standard(self, db_env: Session) -> None:
        settings = _settings()
        original = _install(_success_payload())
        try:
            analysis_worker.generate_report({"indicators": {}}, settings, db=db_env)
        finally:
            httpx.Client = original  # type: ignore[assignment]

        costs = list(db_env.scalars(select(LLMCostRecord)))
        assert costs[0].tier == "TIER_STANDARD"

    def test_degraded_tier_is_recorded_as_effective_tier(self, db_env: Session) -> None:
        """降级后落库的必须是**实际生效**的档位，不是请求的档位。

        否则成本报表会把 LIGHT 的费用算到 DEEP 头上——按 DEEP 归类，
        分层优化的效果就无法度量了。
        """
        settings = _settings(LLM_TIER_DEEP_MODEL=None)
        original = _install(_success_payload())
        try:
            analysis_worker.generate_report(
                {"indicators": {}},
                settings,
                db=db_env,
                tier=ModelTier.TIER_DEEP,
            )
        finally:
            httpx.Client = original  # type: ignore[assignment]

        costs = list(db_env.scalars(select(LLMCostRecord)))
        assert costs[0].tier == "TIER_STANDARD", "落库的必须是实际生效的档位"
        runs = list(db_env.scalars(select(ModelRun)))
        assert runs[0].model_id == "standard-model"

    def test_call_info_reports_degradation(self, db_env: Session) -> None:
        """降级信息必须能回传给调用方（供报告标注）。"""
        settings = _settings(LLM_TIER_DEEP_MODEL=None)
        original = _install(_success_payload())
        call_info: dict = {}
        try:
            analysis_worker.generate_report(
                {"indicators": {}},
                settings,
                db=db_env,
                tier=ModelTier.TIER_DEEP,
                call_info=call_info,
            )
        finally:
            httpx.Client = original  # type: ignore[assignment]

        assert call_info["degraded"] is True
        assert call_info["degraded_reason"] == "TIER_MODEL_UNCONFIGURED"
        assert call_info["tier"] == "TIER_STANDARD"
        assert call_info["requested_tier"] == "TIER_DEEP"

    def test_no_degradation_when_all_configured(self, db_env: Session) -> None:
        settings = _settings()
        original = _install(_success_payload())
        call_info: dict = {}
        try:
            analysis_worker.generate_report(
                {"indicators": {}},
                settings,
                db=db_env,
                tier=ModelTier.TIER_STANDARD,
                call_info=call_info,
            )
        finally:
            httpx.Client = original  # type: ignore[assignment]

        assert call_info["degraded"] is False
        assert call_info["degraded_reason"] is None


class TestLocalFallback:
    """本地不可用时回退云端。"""

    def test_local_transport_error_falls_back_to_cloud(self, db_env: Session) -> None:
        """本地连不上时，必须回退云端并**标注 degraded**。

        静默降级最危险——读者会以为拿到的是本地模型的结论。
        """
        settings = _settings(
            LLM_PROVIDER_LOCAL_MODEL="local-model",
            LLM_PROVIDER_LOCAL_BASE_URL="http://127.0.0.1:1/v1",
        )
        original_client = httpx.Client
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.host)
            # 本地地址直接断开（连不上）
            if request.url.host == "127.0.0.1":
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json=_success_payload("云端兜底"), request=request)

        def patched_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return original_client(*args, **kwargs)

        httpx.Client = patched_client  # type: ignore[assignment]
        call_info: dict = {}
        try:
            content, model_id, _ = analysis_worker.generate_report(
                {"indicators": {}},
                settings,
                db=db_env,
                tier=ModelTier.TIER_LIGHT,
                prefer_local=True,
                call_info=call_info,
            )
        finally:
            httpx.Client = original_client  # type: ignore[assignment]

        assert content.summary == "云端兜底"
        assert model_id == "light-model", "回退后仍用原档位的云端模型"
        assert len(calls) == 2, "应该先试本地、再回退云端"
        assert calls[0] == "127.0.0.1"
        assert calls[1] == "example.invalid"

        # 降级必须留痕
        assert call_info["degraded"] is True
        assert call_info["degraded_reason"] == "LOCAL_UNAVAILABLE"
        assert call_info["provider"] == "cloud"

        # 本地那次失败也要记账（它同样消耗了资源）
        runs = list(db_env.scalars(select(ModelRun).order_by(ModelRun.created_at)))
        assert len(runs) == 2
        assert runs[0].provider == "local"
        assert runs[0].status == "FAILED"
        assert runs[1].provider == "cloud"
        assert runs[1].status == "SUCCESS"

    def test_no_cloud_configured_means_no_fallback(self, db_env: Session) -> None:
        """云端没配置时**不做回退**——回退只会把「本地连不上」
        换成「云端也连不上」，还多记一次失败调用。"""
        settings = _settings(
            LLM_PROVIDER_LOCAL_MODEL="local-model",
            LLM_PROVIDER_LOCAL_BASE_URL="http://127.0.0.1:1/v1",
            LLM_CLOUD_BASE_URL=None,
            LLM_TIER_STANDARD_MODEL=None,
            LLM_TIER_LIGHT_MODEL=None,
        )
        original_client = httpx.Client
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.host)
            raise httpx.ConnectError("connection refused")

        def patched_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return original_client(*args, **kwargs)

        httpx.Client = patched_client  # type: ignore[assignment]
        try:
            with pytest.raises(AICryptoError):
                analysis_worker.generate_report(
                    {"indicators": {}},
                    settings,
                    db=db_env,
                    tier=ModelTier.TIER_LIGHT,
                    prefer_local=True,
                )
        finally:
            httpx.Client = original_client  # type: ignore[assignment]

        assert len(calls) == 1, "不该反复打一个连不上的本地地址"


class TestTierSelection:
    """按触发来源选档位。"""

    def test_retrospective_trigger_selects_deep(self) -> None:
        """复盘 / 日报 / 分歧复盘走 DEEP（PRD 24.3）。"""
        from aicrypto.services.tier_routing import tier_for_trigger

        assert tier_for_trigger("RETROSPECTIVE") is ModelTier.TIER_DEEP
        assert tier_for_trigger("DAILY") is ModelTier.TIER_DEEP
        assert tier_for_trigger("ANOMALY") is ModelTier.TIER_STANDARD
        assert tier_for_trigger("MANUAL") is ModelTier.TIER_STANDARD
