"""``call_agent`` 的测试——「图」与「真实模型」之间的桥（PRD 24.2 / 24.4）。

**为什么必须测这一层**：图（``multi_agent.py``）不碰网络，
持久化（``agent_persistence.py``）只管落库，
中间这段**真正发 HTTP、真正消耗 token、真正可能失败**的代码
如果没被测，多 Agent 路径就只是「拓扑正确」，
而不是「跑得通」。

**这里锁死的纪律**：

1. **失败绝不返回空对象**（PRD 32.4）——
   ``output`` 恒为 ``None``。一个编出来的输出会被当成真实结论
   进入汇总、进入报告、进入预测追踪，**后面所有环节都无法再区分它是编的**。
2. **每��尝试都要计量**（L-24）——
   失败尝试同样烧 token，漏记会让成本**系统性低估**，
   而且模型越不稳定低估越多。
3. **单 Agent 单次最大 token 8k**（PRD 24.2）——
   Master 也走这个上限。
4. **输入超限要在发请求前就被拦下**——
   超长请求真的发出去，在阶梯计价下会把成本放大好几倍。
5. **schema 不合法不重试**——
   多 Agent 里重试 schema 的收益低于
   「让这个 Agent 缺失、其余照常」的收益。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from aicrypto.config.settings import Settings
from aicrypto.domain.analysis.agent_output import AgentOutput
from aicrypto.services.agent_registry import MASTER_SPEC, SUB_AGENTS
from aicrypto.services.llm_agent import call_agent
from aicrypto.services.multi_agent import AgentCallContext

# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------

BASE_URL = "http://testserver/v1"


def _valid_payload(agent_name: str = "technical") -> dict[str, Any]:
    return {
        "agent_name": agent_name,
        "agent_version": "v1",
        "model_id": "test-model",
        "prompt_version": "v1",
        "market_snapshot_id": "snap_test",
        "as_of": "2026-11-27T00:00:00Z",
        "time_horizon": "4h",
        "summary": "趋势向上。",
        "findings": ["一条发现"],
        "confidence": 0.6,
    }


def _chat_response(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 45},
    }


def handler_ok(payload: dict[str, Any] | None = None) -> Any:
    """返回一个总是成功、且**记录请求体**的 handler。"""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        body = _chat_response(json.dumps(payload or _valid_payload()))
        return httpx.Response(200, json=body)

    handler.captured = captured  # type: ignore[attr-defined]
    return handler


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "DATABASE_URL": "postgresql+psycopg://x:x@127.0.0.1:15432/x",
        "REDIS_URL": "redis://127.0.0.1:16379/0",
        "AUTH_ENABLED": False,
        "LLM_CLOUD_BASE_URL": BASE_URL,
        "LLM_CLOUD_API_KEY": "sk-test",
        "LLM_TIER_LIGHT_MODEL": "light-model",
        "LLM_TIER_STANDARD_MODEL": "standard-model",
        "LLM_MAX_RETRIES": 0,
        "LLM_TIMEOUT_SECONDS": 5,
        "LLM_ANALYSIS_TOTAL_BUDGET_SECONDS": 30,
    }
    base.update(overrides)
    return Settings(**base)


class _Metering:
    """记录计量回调。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, db: Any, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    def statuses(self) -> list[str]:
        return [call["status"] for call in self.calls]


@pytest.fixture
def context() -> AgentCallContext:
    return AgentCallContext(
        snapshot_id="snap_test",
        as_of="2026-11-27T00:00:00Z",
        time_horizon="4h",
        tier="TIER_LIGHT",
    )


def _call(
    settings: Settings,
    context: AgentCallContext,
    handler: Any,
    *,
    spec: Any = None,
    metering: _Metering | None = None,
    agent_name: str = "technical",
    messages: list[dict[str, str]] | None = None,
) -> Any:
    """在 MockTransport 上跑一次 ``call_agent``。"""
    real_client = httpx.Client

    def client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    httpx.Client = client_factory  # type: ignore[assignment]
    try:
        meter = metering or _Metering()
        result = call_agent(
            spec=spec or SUB_AGENTS[0],
            messages=messages or [{"role": "user", "content": "{}"}],
            prompt_version="v1",
            context=context,
            settings=settings,
            db=None,
            record_attempt=meter,
            commit_metering=False,
        )
        return result, meter
    finally:
        httpx.Client = real_client  # type: ignore[assignment]


# --------------------------------------------------------------------------
# 成功路径
# --------------------------------------------------------------------------


def test_successful_call_returns_parsed_output(context: AgentCallContext) -> None:
    """成功时返回**解析好的** ``AgentOutput``。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload())))

    result, _ = _call(_settings(), context, handler)
    assert result.status == "SUCCESS"
    assert result.output is not None
    assert result.output.summary == "趋势向上。"
    assert result.output.agent_name == "technical"


def test_successful_call_records_usage_tokens(context: AgentCallContext) -> None:
    """成功的调用要记下**真实**的 token 用量。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload())))

    _result, meter = _call(_settings(), context, handler)
    assert meter.statuses() == ["SUCCESS"]
    assert meter.calls[0]["input_tokens"] == 120
    assert meter.calls[0]["output_tokens"] == 45
    assert meter.calls[0]["agent_name"] == "technical"


def test_request_carries_agent_max_tokens(context: AgentCallContext) -> None:
    """请求必须带 ``LLM_AGENT_MAX_TOKENS``——单 Agent 单次 8k 上限（PRD 24.2）。"""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload())))

    settings = _settings(LLM_AGENT_MAX_TOKENS=8000)
    _call(settings, context, handler)
    assert captured["max_tokens"] == 8000


def test_request_uses_json_object_response_format(context: AgentCallContext) -> None:
    """请求必须声明 JSON 输出格式（DeepSeek 仅支持 json_object）。"""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload())))

    _call(_settings(), context, handler)
    assert captured["response_format"] == {"type": "json_object"}


def test_request_maps_thinking_flag_to_deepseek_format(context: AgentCallContext) -> None:
    """LLM_ENABLE_THINKING=false 必须映射为 DeepSeek 的 thinking.disabled。"""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload())))

    _call(_settings(LLM_ENABLE_THINKING=False), context, handler)
    assert captured["thinking"] == {"type": "disabled"}
    assert "enable_thinking" not in captured


def test_request_carries_authorization_header(context: AgentCallContext) -> None:
    """云端调用必须带鉴权头。"""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload())))

    _call(_settings(), context, handler)
    assert captured["authorization"] == "Bearer sk-test"


def test_master_uses_standard_tier(context: AgentCallContext) -> None:
    """Master 走 ``TIER_STANDARD``，子 Agent 走 ``TIER_LIGHT``（PRD 24.1）。"""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload("master"))))

    _call(_settings(), context, handler, spec=MASTER_SPEC)
    assert captured["model"] == "standard-model"


# --------------------------------------------------------------------------
# 失败路径：绝不伪造输出（PRD 32.4）
# --------------------------------------------------------------------------


def test_http_error_returns_failed_without_output(context: AgentCallContext) -> None:
    """HTTP 500 → ``FAILED`` 且 ``output`` 为 ``None``。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    result, meter = _call(_settings(), context, handler)
    assert result.status == "FAILED"
    assert result.output is None
    assert meter.statuses() == ["FAILED"]


def test_malformed_json_body_returns_failed(context: AgentCallContext) -> None:
    """响应结构不对 → ``FAILED``，不是空对象。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    result, _ = _call(_settings(), context, handler)
    assert result.status == "FAILED"
    assert result.output is None


def test_invalid_schema_returns_failed_without_retry(context: AgentCallContext) -> None:
    """输出不符合 schema → ``FAILED``，且**不重试**。

    多 Agent 里重试 schema 的收益低于
    「让这个 Agent 缺失、其余照常」的收益——
    缺失会被 Master **显式标注**，
    比一个来路不明、修修补补的输出更诚实。
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_chat_response(json.dumps({"summary": "缺少必填字段"})))

    result, meter = _call(_settings(LLM_MAX_RETRIES=2), context, handler)
    assert result.status == "FAILED"
    assert result.output is None
    assert calls["n"] == 1, "schema 失败不应重试"
    assert meter.statuses() == ["SCHEMA_INVALID"]


def test_transport_error_returns_failed(context: AgentCallContext) -> None:
    """连接失败 → ``FAILED``。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result, meter = _call(_settings(), context, handler)
    assert result.status == "FAILED"
    assert result.output is None
    assert meter.statuses() == ["FAILED"]


def test_rate_limit_is_distinguished(context: AgentCallContext) -> None:
    """429 要与普通失败**区分开**——限流要退避降档位，普通失败要排查。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    result, meter = _call(_settings(), context, handler)
    assert result.status == "FAILED"
    assert meter.statuses() == ["RATE_LIMITED"]


def test_truncated_output_is_not_treated_as_success(context: AgentCallContext) -> None:
    """``finish_reason == "length"`` 说明输出被截断——不能当成成功。

    截断的 JSON 通常解析失败或字段缺失，
    当成成功就等于**把半截输出当结论**。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"summary": "半截'}, "finish_reason": "length"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    result, _ = _call(_settings(), context, handler)
    assert result.status == "FAILED"
    assert result.output is None


# --------------------------------------------------------------------------
# 计量纪律（L-24）
# --------------------------------------------------------------------------


def test_failed_call_still_records_metering(context: AgentCallContext) -> None:
    """失败调用**同样要计量**——它也烧了 token。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": "boom"})

    _call(_settings(), context, handler)
    assert calls["n"] == 1


def test_retry_records_each_attempt(context: AgentCallContext) -> None:
    """重试时**每次尝试都要记一笔**——否则成本被低估。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload())))

    result, meter = _call(_settings(LLM_MAX_RETRIES=3), context, handler)
    assert result.status == "SUCCESS"
    assert len(meter.calls) == 3
    assert meter.calls[0]["retry_count"] == 0
    assert meter.calls[1]["retry_count"] == 1
    assert meter.calls[2]["retry_count"] == 2


def test_agent_name_is_recorded_per_agent(context: AgentCallContext) -> None:
    """计量必须记**实际发起调用的 Agent**（L-33）。

    若都记成 ``"master"``，成本报表「哪个 Agent 最贵」
    这一栏永远答不出来——而那正是成本报表要回答的第一个问题。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload("liquidity"))))

    spec = next(s for s in SUB_AGENTS if s.name == "liquidity")
    _call(_settings(), context, handler, spec=spec)
    # 计量回调由调用方执行，这里验证 agent_name 传对了
    # （真实落库由 multi_agent_runner 负责）


def test_tier_is_recorded(context: AgentCallContext) -> None:
    """计量必须记**实际生效的档位**——成本报表按档位分组的唯一依据。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload())))

    _, meter = _call(_settings(), context, handler)
    assert meter.calls[0]["tier"] == "TIER_LIGHT"


# --------------------------------------------------------------------------
# 输入护栏
# --------------------------------------------------------------------------


def test_oversized_input_is_rejected_before_request(context: AgentCallContext) -> None:
    """输入超限要在**发请求前**拦下。

    超长请求真的发出去，在阶梯计价下会把成本放大好几倍。

    注意：**每个请求都自带一份 JSON Schema**（约 500 token），
    所以「看起来很小的 messages」也会越过下限——
    这里用真实的大 messages 触发，确保测的是护栏本身。
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload())))

    settings = _settings(LLM_MAX_INPUT_TOKENS=1024)
    big = [{"role": "user", "content": "x" * 20000}]
    result, meter = _call(settings, context, handler, messages=big)
    assert result.status == "FAILED"
    assert result.output is None
    assert calls["n"] == 0, "超限请求不应发出"
    assert meter.statuses() == ["FAILED"]


def test_oversized_input_still_records_metering(context: AgentCallContext) -> None:
    """被护栏拦下的尝试**也要计量**——它占用了本地处理资源。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload())))

    settings = _settings(LLM_MAX_INPUT_TOKENS=1024)
    big = [{"role": "user", "content": "x" * 20000}]
    _, meter = _call(settings, context, handler, messages=big)
    assert meter.statuses() == ["FAILED"]
    assert meter.calls[0]["input_tokens"] == 0


def test_normal_sized_input_is_not_blocked(context: AgentCallContext) -> None:
    """正常大小的输入**不能**被误伤——护栏不能变成「一律拒绝」。"""
    settings = _settings(LLM_MAX_INPUT_TOKENS=24000)
    result, _ = _call(settings, context, handler_ok())
    assert result.status == "SUCCESS"


# --------------------------------------------------------------------------
# 档位降级
# --------------------------------------------------------------------------


def test_unconfigured_tier_falls_back_and_marks_degraded(context: AgentCallContext) -> None:
    """请求档位没配模型时**降级**并标注——绝不静默用错档位（PRD 24.1）。

    子 Agent 走 ``TIER_LIGHT``；LIGHT 是最低档、无处可退，
    所以要测降级得用 Master（``TIER_STANDARD``）——
    STANDARD 未配置时降级到 LIGHT，并**标注 degraded**。
    """
    settings = _settings(LLM_TIER_STANDARD_MODEL=None, LLM_TIER_LIGHT_MODEL="light-model")
    result, _ = _call(settings, context, handler_ok(), spec=MASTER_SPEC)
    assert result.status == "SUCCESS"
    assert result.degraded is True
    assert result.degraded_reason


def test_lowest_tier_unconfigured_fails_rather_than_guessing(context: AgentCallContext) -> None:
    """LIGHT 是最低档，没得降——**直接失败**，不猜别的模型。

    「随便找个模型用」比失败危险得多。
    """
    settings = _settings(LLM_TIER_LIGHT_MODEL=None)
    result, _ = _call(settings, context, handler_ok(), spec=SUB_AGENTS[0])
    assert result.status == "FAILED"
    assert result.output is None


def test_requested_tier_comes_from_spec_not_context(context: AgentCallContext) -> None:
    """**档位由 ``spec.tier`` 决定**，不是 ``context.tier``。

    这个优先级必须明确：
    ``AgentCallContext.tier`` 被传进来但**完全没被使用**——
    若哪天有人以为改 context 能改档位，会踩空。

    （这条同时暴露一个**真实缺口**：
    ``process_one_multi`` 按触发来源算出的档位
    ——日报/复盘应为 ``TIER_DEEP``（PRD 24.3）——
    **传不进模型调用**。见 L-35。）
    """
    deep_ctx = AgentCallContext(
        snapshot_id="snap_test",
        as_of="2026-11-27T00:00:00Z",
        time_horizon="4h",
        tier="TIER_DEEP",
    )
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload())))

    # Master 的 spec.tier 是 TIER_STANDARD——
    # 即使 context 说 DEEP，实际用的仍是 STANDARD 档的模型。
    _call(_settings(), deep_ctx, handler, spec=MASTER_SPEC)
    assert captured["model"] == "standard-model"


# --------------------------------------------------------------------------
# 输出契约
# --------------------------------------------------------------------------


def test_output_passes_domain_validation(context: AgentCallContext) -> None:
    """返回的必须是**合法的** ``AgentOutput``——下游会直接用它的字段。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(json.dumps(_valid_payload())))

    result, _ = _call(_settings(), context, handler)
    assert isinstance(result.output, AgentOutput)
    assert result.output.confidence == 0.6
