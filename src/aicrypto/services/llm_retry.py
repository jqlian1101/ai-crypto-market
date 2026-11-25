"""LLM 调用的重试与 schema 修复策略（PRD 24.2 / 32.4）。

**为什么单独成模块**：重试策略是**纯逻辑**——判定「这次失败该不该重试」、
算第几次退避多久、拼修复提示词，都不碰 IO。把它从
``analysis_worker`` 里拆出来，才能用纯单元测试覆盖那些最容易出错的分支
（429 要重试、400 不该重试、退避不能超过总预算……）。

**核心原则（三条，都是踩过坑才定下来的）**：

1. **只对可重试错误重试**。429（限流）、5xx（服务端故障）、传输层错误
   （连接被重置、超时）值得重试；400（请求本身不合法）、401（密钥错）、
   schema 校验失败**重试多少次都没用**——重试只是白白烧钱。

2. **退避不能吃掉总预算**。``LLM_ANALYSIS_TOTAL_BUDGET_SECONDS`` 是
   整个分析任务的时间上限。重试的退避等待必须计入这个预算：
   睡眠之前先看「剩下的时间够不够」，不够就直接放弃，
   否则一个卡住的任务会把 worker 的时间预算耗光。

3. **schema 修复只回灌「输出 + 错误」，不重传原始上下文**
   （PRD v2.3 / 成本优化 O12）。若图省事把整个快照重传一遍，
   每次 schema 失败都会让成本翻倍以上——而且修好的概率并没有提高。

**计数口径**：``model_runs.retry_count`` 记录的是**重试次数**，
不是总调用次数。第一次调用失败、第二次成功时 ``retry_count = 1``。
每次实际发出的 HTTP 请求都写一行 ``model_runs``——包括失败的尝试，
因为失败的尝试同样消耗 token，同样要计费。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from aicrypto.common.errors import AICryptoError, ErrorCode

#: 触发退避的 HTTP 状态码。
#: - 429：限流
#: - 5xx：供应商服务端故障
#: 其余（4xx）视为「请求本身有问题」，重试无意义。
RETRYABLE_HTTP_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})

#: 退避基数（秒）。第 n 次重试等待 ``BASE * 2**(n-1)``，再乘抖动系数。
RETRY_BACKOFF_BASE_SECONDS: float = 1.0

#: 退避上限（秒），防止指数退避把总预算吃光。
RETRY_BACKOFF_MAX_SECONDS: float = 30.0


class AttemptOutcome(StrEnum):
    """一次 HTTP 尝试的结果分类。"""

    SUCCESS = "SUCCESS"
    #: 可重试：限流 / 5xx / 传输层错误
    RETRYABLE = "RETRYABLE"
    #: 不可重试：4xx 请求错误
    FATAL = "FATAL"
    #: schema 校验失败——交给「修复」流程处理，不走普通重试
    SCHEMA_INVALID = "SCHEMA_INVALID"
    #: 输出被截断（finish_reason == length）
    TRUNCATED = "TRUNCATED"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """重试策略（不可变，便于测试与传递）。

    ``max_retries`` 是**额外**的重试次数：0 表示只调用一次。
    """

    max_retries: int
    backoff_base_seconds: float = RETRY_BACKOFF_BASE_SECONDS
    backoff_max_seconds: float = RETRY_BACKOFF_MAX_SECONDS

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries 不能为负")

    def backoff_seconds(self, attempt: int) -> float:
        """第 ``attempt`` 次重试前应等待的秒数（``attempt`` 从 1 起）。

        指数退避 + 上限封顶。**不做随机抖动**：抖动会让测试不确定，
        而这里的退避基数已经足够小（默认 1s），抖动带来的收益有限。
        """
        if attempt < 1:
            raise ValueError("attempt 从 1 起")
        delay = self.backoff_base_seconds * (2 ** (attempt - 1))
        return min(delay, self.backoff_max_seconds)

    def total_backoff_seconds(self) -> float:
        """把所有重试的退避时间加起来。

        用于「退避是否会吃掉整个总预算」的判断。
        """
        return sum(self.backoff_seconds(n) for n in range(1, self.max_retries + 1))


def classify_http_status(status_code: int) -> AttemptOutcome:
    """判定一个 HTTP 状态码属于哪类结果。"""
    if 200 <= status_code < 300:
        return AttemptOutcome.SUCCESS
    if status_code in RETRYABLE_HTTP_STATUS:
        return AttemptOutcome.RETRYABLE
    return AttemptOutcome.FATAL


def is_retryable_error(exc: BaseException) -> bool:
    """判定一个**传输层**异常是否值得重试。

    连接被重置、连接超时、读写超时、远端断开——这些重试有意义。
    但 ``httpx.InvalidURL`` / ``httpx.UnsupportedProtocol`` 之类的
    **请求本身构造错误**重试多少次都一样。
    """
    import httpx

    if isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        return False
    return isinstance(exc, httpx.HTTPError)


def should_retry(
    *,
    outcome: AttemptOutcome,
    attempt: int,
    policy: RetryPolicy,
    deadline: dt.datetime | None = None,
    pending_backoff_seconds: float = 0.0,
) -> bool:
    """判定「当前这次失败之后还要不要再试」。

    ``attempt`` 是**已经发出的请求次数**（从 1 起）。
    ``deadline`` 是整个分析任务的时间上限；退避等待必须落在它之内，
    否则这次重试会让任务整体超时——那还不如早点失败，
    把「已完成的部分」交出去。
    """
    if outcome is not AttemptOutcome.RETRYABLE:
        return False
    if attempt > policy.max_retries:
        return False
    if deadline is None:
        return True
    now = dt.datetime.now(dt.UTC)
    next_backoff = policy.backoff_seconds(attempt)
    return (now + dt.timedelta(seconds=next_backoff)) <= deadline


def build_repair_messages(
    *,
    system_template_content: str,
    original_output: str,
    error_summary: str,
) -> list[dict[str, str]]:
    """拼出「schema 修复」的请求消息。

    **只回灌「模型上一次的输出」+「错误摘要」**（PRD v2.3 / 成本优化 O12），
    **不重传原始快照上下文**。

    为什么不重传上下文：快照本身动辄上万 token，重传一次就等于
    把这次修复的成本翻倍——而模型需要修正的只是**它自己输出里那几个字段**，
    不需要重新看一遍行情。重传了不但更贵，还可能让模型「重新分析一遍」
    从而给出与上次不同的结论，破坏可复现性。

    注意这里刻意**不**复用原始 user 模板：那个模板带 ``{snapshot}``
    占位符，会把整个快照重新塞回去。
    """
    repair_prompt = (
        "你上一次的输出不符合要求的 JSON Schema。\n"
        "请**只修正**下列错误，不要重新分析、不要改变已有结论、"
        "不要新增内容。\n"
        "必须输出一个 JSON 对象（禁止根数组），且通过给定 Schema 校验。\n\n"
        f"【你上一次的输出】\n{original_output}\n\n"
        f"【校验错误】\n{error_summary}"
    )
    return [
        {"role": "system", "content": system_template_content},
        {"role": "user", "content": repair_prompt},
    ]


def summarize_validation_issues(issues: list[dict[str, Any]], *, limit: int = 12) -> str:
    """把 pydantic 的校验错误压成给模型看的**简短**文本。

    只保留「字段路径 + 错误类型 + 期望值」，不把输入值也带上去——
    模型看到自己的错误输出就够了，重复粘贴原始值会无谓增加 token。
    """
    lines: list[str] = []
    for issue in issues[:limit]:
        loc = ".".join(str(part) for part in issue.get("loc", ())) or "(根)"
        lines.append(f"- {loc}: {issue.get('type', 'invalid')}")
    return "\n".join(lines)


def exceeds_input_token_limit(
    *,
    input_tokens: int,
    limit: int,
) -> bool:
    """判断输入 token 是否超出上限。

    ``limit <= 0`` 表示**不限**（关闭护栏）。
    """
    if limit <= 0:
        return False
    return input_tokens > limit


def build_input_limit_error(*, actual: int, limit: int) -> AICryptoError:
    """构造「输入超限」的错误。

    单价有「输入超过 100K 跳价 ×5」这类阶梯定价（成本优化 O11），
    所以超限不是性能问题而是**成本问题**，必须在发请求前拦下来。
    """
    return AICryptoError(
        ErrorCode.LLM_BUDGET_EXCEEDED,
        details={"stage": "input_token_limit", "actual": actual, "limit": limit},
    )


__all__ = [
    "RETRYABLE_HTTP_STATUS",
    "RETRY_BACKOFF_BASE_SECONDS",
    "RETRY_BACKOFF_MAX_SECONDS",
    "AttemptOutcome",
    "RetryPolicy",
    "build_input_limit_error",
    "build_repair_messages",
    "classify_http_status",
    "exceeds_input_token_limit",
    "is_retryable_error",
    "should_retry",
    "summarize_validation_issues",
]
