"""模型分层路由的纯逻辑测试（PRD 24.1 / 24.3）。

**这些分支最容易出错的三个地方**：

1. **降级链的顺序**——DEEP 降级时**不能**掉到 LIGHT。
   PRD 24.1 明确「不要降级这一档」：拿 LIGHT 档的模型冒充 DEEP 的
   结果，比直接失败更糟——读者会以为这是经过深思熟虑的结论。
2. **降级必须留痕**——静默降级最危险。读者会以为拿到的是原档位的结论。
3. **全部未配置时必须抛错**——不能「随便找个模型用」。
   静默用错档位比失败危险得多。
"""

from __future__ import annotations

import pytest

from aicrypto.common.errors import AICryptoError, ErrorCode
from aicrypto.config.settings import Settings
from aicrypto.domain.analysis.agent_output import ModelTier
from aicrypto.services.tier_routing import (
    TierFallbackReason,
    fallback_tiers,
    model_for_tier,
    resolve_tier_route,
    tier_for_trigger,
    tier_order,
)


def _settings(**kwargs) -> Settings:
    base = {
        "_env_file": None,
        "DATABASE_URL": "postgresql+psycopg://x:x@127.0.0.1:15432/x",
        "REDIS_URL": "redis://127.0.0.1:16379/0",
        "AUTH_ENABLED": False,
        "LLM_CLOUD_BASE_URL": "https://cloud.invalid/v1",
        "LLM_CLOUD_API_KEY": "cloud-key",
        "LLM_TIER_LIGHT_MODEL": "light-model",
        "LLM_TIER_STANDARD_MODEL": "standard-model",
        "LLM_TIER_DEEP_MODEL": "deep-model",
    }
    base.update(kwargs)
    return Settings(**base)


class TestModelForTier:
    """档位 -> 模型映射。"""

    def test_each_tier_maps_to_its_own_model(self) -> None:
        s = _settings()
        assert model_for_tier(s, ModelTier.TIER_LIGHT) == "light-model"
        assert model_for_tier(s, ModelTier.TIER_STANDARD) == "standard-model"
        assert model_for_tier(s, ModelTier.TIER_DEEP) == "deep-model"

    def test_unconfigured_tier_returns_none(self) -> None:
        s = _settings(LLM_TIER_DEEP_MODEL=None)
        assert model_for_tier(s, ModelTier.TIER_DEEP) is None


class TestFallbackTiers:
    """降级链。"""

    def test_light_has_no_fallback(self) -> None:
        """LIGHT 已是最低档，无处可退。"""
        assert fallback_tiers(ModelTier.TIER_LIGHT) == []

    def test_standard_falls_back_to_light(self) -> None:
        assert fallback_tiers(ModelTier.TIER_STANDARD) == [ModelTier.TIER_LIGHT]

    def test_deep_does_not_fall_back_to_light(self) -> None:
        """**DEEP 绝不降级到 LIGHT**（PRD 24.1：不要降级这一档）。

        拿 LIGHT 档的模型冒充 DEEP 的结果比直接失败更糟——
        读者会以为这是经过深思熟虑的结论。
        """
        assert ModelTier.TIER_LIGHT not in fallback_tiers(ModelTier.TIER_DEEP)

    def test_deep_falls_back_to_standard_only(self) -> None:
        assert fallback_tiers(ModelTier.TIER_DEEP) == [ModelTier.TIER_STANDARD]

    def test_tier_order(self) -> None:
        assert tier_order(ModelTier.TIER_LIGHT) < tier_order(ModelTier.TIER_STANDARD)
        assert tier_order(ModelTier.TIER_STANDARD) < tier_order(ModelTier.TIER_DEEP)


class TestResolveTierRoute:
    """路由解析。"""

    def test_uses_requested_tier_when_configured(self) -> None:
        s = _settings()
        route = resolve_tier_route(s, requested_tier=ModelTier.TIER_LIGHT)
        assert route.tier is ModelTier.TIER_LIGHT
        assert route.model_id == "light-model"
        assert route.provider == "cloud"
        assert route.degraded is False
        assert route.degraded_reason is None

    def test_deep_tier_is_reachable(self) -> None:
        """DEEP 档位现在真的能被用到（此前硬编码 TIER_STANDARD）。"""
        s = _settings()
        route = resolve_tier_route(s, requested_tier=ModelTier.TIER_DEEP)
        assert route.tier is ModelTier.TIER_DEEP
        assert route.model_id == "deep-model"

    def test_unconfigured_tier_falls_back_and_marks_degraded(self) -> None:
        """DEEP 未配置 -> 降级到 STANDARD，**且必须标注 degraded**。"""
        s = _settings(LLM_TIER_DEEP_MODEL=None)
        route = resolve_tier_route(s, requested_tier=ModelTier.TIER_DEEP)
        assert route.tier is ModelTier.TIER_STANDARD
        assert route.requested_tier is ModelTier.TIER_DEEP
        assert route.degraded is True
        assert route.degraded_reason == str(TierFallbackReason.TIER_MODEL_UNCONFIGURED)

    def test_standard_unconfigured_falls_back_to_light(self) -> None:
        s = _settings(LLM_TIER_STANDARD_MODEL=None)
        route = resolve_tier_route(s, requested_tier=ModelTier.TIER_STANDARD)
        assert route.tier is ModelTier.TIER_LIGHT
        assert route.degraded is True

    def test_all_tiers_unconfigured_raises(self) -> None:
        """全部未配置时必须抛错——**不能随便找个模型用**。

        静默用错档位比失败危险得多。
        """
        s = _settings(
            LLM_TIER_LIGHT_MODEL=None,
            LLM_TIER_STANDARD_MODEL=None,
            LLM_TIER_DEEP_MODEL=None,
        )
        with pytest.raises(AICryptoError) as excinfo:
            resolve_tier_route(s, requested_tier=ModelTier.TIER_STANDARD)
        assert excinfo.value.code == ErrorCode.LLM_UNAVAILABLE
        assert excinfo.value.details["requested_tier"] == "TIER_STANDARD"

    def test_light_unconfigured_raises(self) -> None:
        """LIGHT 是最低档，没得降——直接失败。"""
        s = _settings(LLM_TIER_LIGHT_MODEL=None)
        with pytest.raises(AICryptoError) as excinfo:
            resolve_tier_route(s, requested_tier=ModelTier.TIER_LIGHT)
        assert excinfo.value.code == ErrorCode.LLM_UNAVAILABLE


class TestLocalPreference:
    """本地优先与回退。"""

    def test_local_used_when_preferred_and_configured(self) -> None:
        s = _settings(
            LLM_PROVIDER_LOCAL_MODEL="local-model",
            LLM_PROVIDER_LOCAL_BASE_URL="http://ollama:11434/v1",
        )
        route = resolve_tier_route(s, requested_tier=ModelTier.TIER_LIGHT, prefer_local=True)
        assert route.provider == "local"
        assert route.model_id == "local-model"
        assert route.base_url == "http://ollama:11434/v1"
        assert route.degraded is False

    def test_local_missing_falls_back_to_cloud_with_degraded(self) -> None:
        """本地没配模型 -> 回退云端，**档位不变**但必须标注 degraded。

        注意：这与「档位降级」是两回事——档位没变，只是供应商变了。
        """
        s = _settings(LLM_PROVIDER_LOCAL_MODEL=None)
        route = resolve_tier_route(s, requested_tier=ModelTier.TIER_STANDARD, prefer_local=True)
        assert route.provider == "cloud"
        assert route.tier is ModelTier.TIER_STANDARD
        assert route.requested_tier is ModelTier.TIER_STANDARD
        assert route.degraded is True
        assert route.degraded_reason == str(TierFallbackReason.TIER_MODEL_UNCONFIGURED)

    def test_local_missing_still_falls_back_across_tiers(self) -> None:
        """**回归**：本地没配 + 目标档位也没配 -> 仍要沿降级链降级。

        曾经的缺陷：``prefer_local`` 分支直接调 ``_cloud_route()``，
        **绕过了降级链**，于是返回**空模型名**——请求带着空 ``model``
        发出去，失败原因还很难定位。
        """
        s = _settings(LLM_PROVIDER_LOCAL_MODEL=None, LLM_TIER_DEEP_MODEL=None)
        route = resolve_tier_route(s, requested_tier=ModelTier.TIER_DEEP, prefer_local=True)
        assert route.model_id == "standard-model", "必须沿降级链找到 STANDARD"
        assert route.tier is ModelTier.TIER_STANDARD
        assert route.degraded is True

    def test_local_missing_and_all_tiers_unconfigured_raises(self) -> None:
        """**回归**：本地没配 + 全部档位都没配 -> 必须抛错，**不能返回空模型名**。

        空模型名是「静默用错档位」的另一种形式：请求会带着空的
        ``model`` 字段发出去，失败原因极难定位。
        """
        s = _settings(
            LLM_PROVIDER_LOCAL_MODEL=None,
            LLM_TIER_LIGHT_MODEL=None,
            LLM_TIER_STANDARD_MODEL=None,
            LLM_TIER_DEEP_MODEL=None,
        )
        with pytest.raises(AICryptoError) as excinfo:
            resolve_tier_route(s, requested_tier=ModelTier.TIER_DEEP, prefer_local=True)
        assert excinfo.value.code == ErrorCode.LLM_UNAVAILABLE

    def test_route_never_has_empty_model_id(self) -> None:
        """**任何可解析出的路由，模型名都不得为空**。"""
        combos = [
            ({"LLM_PROVIDER_LOCAL_MODEL": None, "LLM_TIER_DEEP_MODEL": None}, True),
            ({"LLM_PROVIDER_LOCAL_MODEL": "local", "LLM_TIER_DEEP_MODEL": None}, True),
            ({"LLM_TIER_DEEP_MODEL": None}, False),
            ({}, False),
        ]
        for overrides, prefer_local in combos:
            s = _settings(**overrides)
            try:
                route = resolve_tier_route(
                    s, requested_tier=ModelTier.TIER_DEEP, prefer_local=prefer_local
                )
            except AICryptoError:
                continue
            assert route.model_id, f"空模型名：overrides={overrides}"

    def test_local_api_key_is_optional(self) -> None:
        """本地 Ollama 默认无鉴权——不能因为没配 key 就拒绝。"""
        s = _settings(LLM_PROVIDER_LOCAL_MODEL="local-model", LLM_PROVIDER_LOCAL_API_KEY=None)
        route = resolve_tier_route(s, requested_tier=ModelTier.TIER_LIGHT, prefer_local=True)
        assert route.api_key is None
        assert route.provider == "local"


class TestRouteSerialization:
    """路由信息的序列化。"""

    def test_as_dict_excludes_api_key(self) -> None:
        """**绝不泄露 API Key**——这段字典会被写进报告 / 日志。"""
        s = _settings()
        route = resolve_tier_route(s, requested_tier=ModelTier.TIER_STANDARD)
        payload = route.as_dict()
        assert "api_key" not in payload
        assert "cloud-key" not in str(payload)
        assert payload["model_id"] == "standard-model"
        assert payload["tier"] == "TIER_STANDARD"

    def test_as_dict_carries_degradation(self) -> None:
        s = _settings(LLM_TIER_DEEP_MODEL=None)
        route = resolve_tier_route(s, requested_tier=ModelTier.TIER_DEEP)
        payload = route.as_dict()
        assert payload["degraded"] is True
        assert payload["requested_tier"] == "TIER_DEEP"
        assert payload["tier"] == "TIER_STANDARD"


class TestTierForTrigger:
    """按触发来源决定档位。"""

    @pytest.mark.parametrize("trigger", ["MANUAL", "SCHEDULED", "ANOMALY"])
    def test_normal_triggers_use_standard(self, trigger: str) -> None:
        """手动 / 定时 / 异动都是 STANDARD。

        异动触发尤其不能降级——异动解释需要准确的 Bias 与概率。
        """
        assert tier_for_trigger(trigger) is ModelTier.TIER_STANDARD

    @pytest.mark.parametrize("trigger", ["RETROSPECTIVE", "DAILY", "WEEKLY", "REVIEW", "DEEP"])
    def test_review_triggers_use_deep(self, trigger: str) -> None:
        """日报 / 周报 / 分歧复盘用 DEEP（PRD 24.3）。"""
        assert tier_for_trigger(trigger) is ModelTier.TIER_DEEP

    def test_retrospective_flag_overrides_trigger(self) -> None:
        """显式意图优先于从 trigger 字符串里猜。"""
        assert tier_for_trigger("MANUAL", retrospective=True) is ModelTier.TIER_DEEP

    def test_trigger_is_case_insensitive(self) -> None:
        assert tier_for_trigger("daily") is ModelTier.TIER_DEEP
        assert tier_for_trigger("  DAILY  ") is ModelTier.TIER_DEEP

    def test_empty_trigger_defaults_to_standard(self) -> None:
        assert tier_for_trigger("") is ModelTier.TIER_STANDARD
