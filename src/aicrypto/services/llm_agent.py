"""多 Agent 的 LLM 调用实现（PRD 24.2 / 24.4）。

**这个模块是「图」与「真实模型」之间的桥**。
图的拓扑与纪律在 ``services/multi_agent.py`` 里（不碰网络），
这里负责**发请求、计量**。

**复用而不是复制**：
计量走 ``services/analysis_worker.py::record_model_run``——
与单 Agent 路径**同一个**实现，因此
「多 Agent 的成本能被成本报表看到」这件事
**不需要额外接线**，它天然成立。
若这里另写一套计量，多 Agent 的调用就会**不进成本报表**，
而那正是 L-31 刚刚修好的问题（成本数据有表有写入但没人读）——
**新的调用路径必须走同一条计量路径**，否则报表会漏掉它。

**每次尝试独立提交**（L-24）：
多 Agent 的并发度是 6 倍，失败尝试的成本占比更高。
计量行**立即提交**，不与报告事务绑定——
否则报告回滚时，失败子 Agent 烧掉的钱会一起消失。

**失败绝不伪造输出**（PRD 32.4）：
调用失败 / schema 不合法 → 返回 ``status=FAILED`` 且 ``output=None``，
**不返回空对象**。Master 会收到「缺失名单」。

**档位**（PRD 24.1）：
子 Agent 用 ``TIER_LIGHT``（偏确定性、抽取分类类任务），
Master 用 ``TIER_STANDARD``（综合推理）。
``TIER_DEEP`` 不由本模块触发——日报/周报场景由调用方指定。

**schema 不重试**（与单 Agent 路径的差异，**故意的**）：
单 Agent 路径会做一次「schema 修复」（回灌输出 + 错误）。
多 Agent 里**不做**：缺失会被 Master **显式标注**，
比一个来路不明、修修补补的输出更诚实；
而且 6 个分支各自重试会把成本放大好几倍。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from aicrypto.common.errors import AICryptoError, ErrorCode
from aicrypto.config.settings import Settings
from aicrypto.domain.analysis.agent_output import AgentOutput, ModelTier
from aicrypto.services.agent_registry import AgentSpec
from aicrypto.services.llm_retry import (
    AttemptOutcome,
    RetryPolicy,
    build_input_limit_error,
    classify_http_status,
    exceeds_input_token_limit,
    is_retryable_error,
    should_retry,
)
from aicrypto.services.multi_agent import AgentCallContext, AgentResult, parse_agent_output
from aicrypto.services.tier_routing import resolve_tier_route

#: 每次尝试的记录回调——由调用方注入（通常是 ``analysis_worker.record_model_run``）。
#:
#: 做成回调而非直接 import，是为了**不制造循环依赖**
#: （``analysis_worker`` 不 import 本模块，但本模块需要它的计量逻辑）。
MeteringRecorder = Callable[..., None]


def _extract_output(response: httpx.Response) -> tuple[str | None, AttemptOutcome]:
    """从响应里取出模型输出文本并判定结果分类。"""
    if not response.is_success:
        return None, classify_http_status(response.status_code)
    try:
        choice = response.json()["choices"][0]
    except (KeyError, IndexError, TypeError, ValueError):
        return None, AttemptOutcome.SCHEMA_INVALID
    if choice.get("finish_reason") == "length":
        return None, AttemptOutcome.TRUNCATED
    try:
        content = choice["message"]["content"]
    except (KeyError, TypeError):
        return None, AttemptOutcome.SCHEMA_INVALID
    if not isinstance(content, str):
        return None, AttemptOutcome.SCHEMA_INVALID
    return content, AttemptOutcome.SUCCESS


def _extract_usage(response: httpx.Response) -> tuple[int, int]:
    """取 token 用量。缺失时返回 ``(0, 0)``——表示「用量未知」。

    **不能把「未知」当成 0**：那会让成本报表少算这一次的费用。
    与 L-05「键缺失 ≠ 费率为 0」同源。
    """
    try:
        usage = response.json()["usage"]
        return int(usage["prompt_tokens"]), int(usage["completion_tokens"])
    except (KeyError, IndexError, TypeError, ValueError):
        return 0, 0


def _estimate_input_tokens(payload: dict[str, Any]) -> int:
    """粗估输入 token 数（与单 Agent 路径同一口径）。

    宁可**高估**也不低估：输入超限会让请求发不出去，
    而低估会让超长请求真的发出去，在阶梯计价下把成本放大好几倍。
    """
    return len(json.dumps(payload, ensure_ascii=False)) // 4 + 1


def call_agent(
    *,
    spec: AgentSpec,
    messages: list[dict[str, str]],
    prompt_version: str,
    context: AgentCallContext,
    settings: Settings,
    db: Any,
    record_attempt: MeteringRecorder,
    commit_metering: bool = True,
) -> AgentResult:
    """调用一次模型，返回该 Agent 的执行结果。

    **永不抛异常**——失败以 ``status=FAILED`` 返回。
    一个子 Agent 失败**不应**让整次分析崩溃：
    PRD 24.4 要求超时输出「分析超时 + 已完成部分」，
    而 PRD 32.4 要求「如实登记该 Agent 缺失」而不是伪造输出。
    抛异常会直接丢掉其他 5 个 Agent 已经算好的结果。
    """
    requested_tier = _tier_for(spec)
    try:
        route = resolve_tier_route(settings, requested_tier=requested_tier)
    except AICryptoError as exc:
        return _failed(spec, exc.code, {"stage": "routing", "error": str(exc)})

    provider = route.provider
    model = route.model_id
    base = route.base_url
    secret = route.api_key
    tier_label = str(route.tier)
    degraded = route.degraded
    degraded_reason = route.degraded_reason
    if not base or not model:
        return _failed(spec, ErrorCode.LLM_UNAVAILABLE, {"stage": "routing"})

    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    schema = AgentOutput.model_json_schema()

    payload: dict[str, Any] = {
        "model": model,
        **(
            {"enable_thinking": settings.LLM_ENABLE_THINKING}
            if settings.LLM_ENABLE_THINKING is not None
            else {}
        ),
        # **单个 Agent 单次最大 token 8k**（PRD 24.2）。
        # Master 也走这个上限——它不是「一个大请求」，而是一次 Agent 调用。
        "max_tokens": settings.LLM_AGENT_MAX_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "agent_output", "schema": schema},
        },
        "messages": messages,
    }

    estimated_input = _estimate_input_tokens(payload)
    if exceeds_input_token_limit(input_tokens=estimated_input, limit=settings.LLM_MAX_INPUT_TOKENS):
        error = build_input_limit_error(
            actual=estimated_input,
            limit=settings.LLM_MAX_INPUT_TOKENS,
        )
        record_attempt(
            db,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            status="FAILED",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            retry_count=0,
            error={"stage": "input_limit", "error": str(error)},
            tier=tier_label,
            agent_name=spec.name,
            commit=commit_metering,
        )
        return _failed(spec, error.code, {"stage": "input_limit"})

    policy = RetryPolicy(max_retries=settings.LLM_MAX_RETRIES)
    timeout = min(settings.LLM_TIMEOUT_SECONDS, settings.LLM_ANALYSIS_TOTAL_BUDGET_SECONDS)
    attempt = 0

    while True:
        attempt += 1
        call_started = time.perf_counter()
        try:
            with httpx.Client(timeout=timeout, trust_env=settings.LLM_TRUST_ENV) as client:
                response = client.post(
                    f"{base.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
        except httpx.RequestError as exc:
            latency_ms = int((time.perf_counter() - call_started) * 1000)
            record_attempt(
                db,
                provider=provider,
                model=model,
                prompt_version=prompt_version,
                status="FAILED",
                input_tokens=0,
                output_tokens=0,
                latency_ms=latency_ms,
                retry_count=attempt - 1,
                error={"stage": "transport", "error_type": type(exc).__name__},
                tier=tier_label,
                agent_name=spec.name,
                commit=commit_metering,
            )
            outcome = AttemptOutcome.RETRYABLE if is_retryable_error(exc) else AttemptOutcome.FATAL
            if not should_retry(outcome=outcome, attempt=attempt, policy=policy):
                return _failed(
                    spec,
                    ErrorCode.LLM_UNAVAILABLE,
                    {"stage": "transport", "error_type": type(exc).__name__},
                    model_id=model,
                    prompt_version=prompt_version,
                    tier=tier_label,
                    degraded=degraded,
                    degraded_reason=degraded_reason,
                )
            time.sleep(policy.backoff_seconds(attempt))
            continue

        latency_ms = int((time.perf_counter() - call_started) * 1000)
        input_tokens, output_tokens = _extract_usage(response)
        output, outcome = _extract_output(response)

        if outcome is not AttemptOutcome.SUCCESS:
            # **429 单独标记**：限流与普通失败在运维上要区分处理
            # （限流要退避、降档位；普通失败要排查）。
            status = "RATE_LIMITED" if response.status_code == 429 else "FAILED"
            if outcome is AttemptOutcome.SCHEMA_INVALID:
                status = "SCHEMA_INVALID"
            record_attempt(
                db,
                provider=provider,
                model=model,
                prompt_version=prompt_version,
                status=status,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                retry_count=attempt - 1,
                error={"stage": "provider_http", "outcome": str(outcome)},
                tier=tier_label,
                agent_name=spec.name,
                commit=commit_metering,
            )
            if not should_retry(outcome=outcome, attempt=attempt, policy=policy):
                return _failed(
                    spec,
                    ErrorCode.LLM_UNAVAILABLE,
                    {"stage": "provider_http", "outcome": str(outcome)},
                    model_id=model,
                    prompt_version=prompt_version,
                    tier=tier_label,
                    degraded=degraded,
                    degraded_reason=degraded_reason,
                )
            time.sleep(policy.backoff_seconds(attempt))
            continue

        assert output is not None  # 类型收窄，非运行时断言
        try:
            parsed = parse_agent_output(output)
        except (ValidationError, ValueError) as exc:
            record_attempt(
                db,
                provider=provider,
                model=model,
                prompt_version=prompt_version,
                status="SCHEMA_INVALID",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                retry_count=attempt - 1,
                error={"stage": "output_validation"},
                tier=tier_label,
                agent_name=spec.name,
                commit=commit_metering,
            )
            # **schema 不重试**（PRD 32.4：修复也失败就标记 FAILED）。
            # 与单 Agent 路径不同——多 Agent 里重试 schema 的收益低于
            # 「让这个 Agent 缺失、其余照常」的收益：
            # 缺失会被 Master **显式标注**，比一个来路不明的输出更诚实。
            return _failed(
                spec,
                ErrorCode.LLM_SCHEMA_INVALID,
                {"stage": "output_validation", "error": str(exc)[:400]},
                model_id=model,
                prompt_version=prompt_version,
                tier=tier_label,
                degraded=degraded,
                degraded_reason=degraded_reason,
            )

        record_attempt(
            db,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            status="SUCCESS",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            retry_count=attempt - 1,
            error=None,
            tier=tier_label,
            agent_name=spec.name,
            commit=commit_metering,
        )
        return AgentResult(
            agent_name=spec.name,
            status="SUCCESS",
            output=parsed,
            duration_ms=latency_ms,
            model_id=model,
            prompt_version=prompt_version,
            tier=tier_label,
            degraded=degraded,
            degraded_reason=degraded_reason,
        )


def _tier_for(spec: AgentSpec) -> ModelTier:
    """把 Agent 定义里的档位字符串转成枚举。"""
    try:
        return ModelTier(spec.tier)
    except ValueError:
        return ModelTier.TIER_LIGHT


def _failed(
    spec: AgentSpec,
    code: str,
    error: dict[str, Any],
    *,
    model_id: str = "",
    prompt_version: str = "",
    tier: str = "",
    degraded: bool = False,
    degraded_reason: str | None = None,
) -> AgentResult:
    """构造一个失败结果。

    **``output`` 恒为 ``None``**——失败就是没有输出。
    绝不用空对象冒充成功（PRD 32.4）。
    """
    return AgentResult(
        agent_name=spec.name,
        status="FAILED",
        output=None,
        error={"code": code, **error},
        duration_ms=0,
        model_id=model_id,
        prompt_version=prompt_version,
        tier=tier or spec.tier,
        degraded=degraded,
        degraded_reason=degraded_reason,
    )


__all__ = ["MeteringRecorder", "call_agent"]
