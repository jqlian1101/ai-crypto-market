"""LLM 重试与 schema 修复策略的纯逻辑测试（PRD 24.2 / 32.4）。

**为什么这些逻辑值得单独测**：重试判定是那种「平时不出事、
出事就烧钱」的代码——重试了不该重试的（400），白烧一次调用；
该重试的没重试（429），任务直接失败；退避吃掉了总预算，
一个卡住的任务把 worker 拖死。这几条分支都必须锁死。
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest

from aicrypto.common.errors import AICryptoError, ErrorCode
from aicrypto.services.llm_retry import (
    RETRY_BACKOFF_MAX_SECONDS,
    AttemptOutcome,
    RetryPolicy,
    build_input_limit_error,
    build_repair_messages,
    classify_http_status,
    exceeds_input_token_limit,
    is_retryable_error,
    should_retry,
    summarize_validation_issues,
)


class TestClassifyHttpStatus:
    """HTTP 状态码分类。"""

    @pytest.mark.parametrize("code", [200, 201, 204])
    def test_2xx_is_success(self, code: int) -> None:
        assert classify_http_status(code) is AttemptOutcome.SUCCESS

    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
    def test_retryable_statuses(self, code: int) -> None:
        assert classify_http_status(code) is AttemptOutcome.RETRYABLE

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
    def test_4xx_is_fatal(self, code: int) -> None:
        """4xx 是「请求本身有问题」，重试多少次都一样。"""
        assert classify_http_status(code) is AttemptOutcome.FATAL


class TestIsRetryableError:
    """传输层异常的可重试性。"""

    def test_connect_error_is_retryable(self) -> None:
        exc = httpx.ConnectError("connection refused")
        assert is_retryable_error(exc) is True

    def test_read_timeout_is_retryable(self) -> None:
        assert is_retryable_error(httpx.ReadTimeout("timeout")) is True

    def test_connect_timeout_is_retryable(self) -> None:
        assert is_retryable_error(httpx.ConnectTimeout("timeout")) is True

    def test_remote_protocol_error_is_retryable(self) -> None:
        assert is_retryable_error(httpx.RemoteProtocolError("bad chunk")) is True

    def test_invalid_url_is_not_retryable(self) -> None:
        """URL 本身不合法，重试多少次都一样。"""
        exc = httpx.InvalidURL("not a url")
        assert is_retryable_error(exc) is False

    def test_unsupported_protocol_is_not_retryable(self) -> None:
        assert is_retryable_error(httpx.UnsupportedProtocol("nope")) is False

    def test_non_httpx_exception_is_not_retryable(self) -> None:
        assert is_retryable_error(ValueError("x")) is False


class TestRetryPolicy:
    """退避策略。"""

    def test_negative_max_retries_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_retries"):
            RetryPolicy(max_retries=-1)

    def test_backoff_is_exponential(self) -> None:
        policy = RetryPolicy(max_retries=3, backoff_base_seconds=1.0)
        assert policy.backoff_seconds(1) == 1.0
        assert policy.backoff_seconds(2) == 2.0
        assert policy.backoff_seconds(3) == 4.0

    def test_backoff_is_capped(self) -> None:
        """指数退避必须有上限，否则第 10 次重试要等 512 秒。"""
        policy = RetryPolicy(max_retries=10, backoff_base_seconds=1.0)
        assert policy.backoff_seconds(10) == RETRY_BACKOFF_MAX_SECONDS

    def test_attempt_must_start_at_one(self) -> None:
        policy = RetryPolicy(max_retries=1)
        with pytest.raises(ValueError, match="attempt"):
            policy.backoff_seconds(0)

    def test_total_backoff_sums_all_retries(self) -> None:
        policy = RetryPolicy(max_retries=3, backoff_base_seconds=1.0)
        assert policy.total_backoff_seconds() == pytest.approx(1.0 + 2.0 + 4.0)

    def test_zero_retries_has_no_backoff(self) -> None:
        assert RetryPolicy(max_retries=0).total_backoff_seconds() == 0.0


class TestShouldRetry:
    """是否该重试的判定。"""

    def test_success_is_never_retried(self) -> None:
        policy = RetryPolicy(max_retries=3)
        assert (
            should_retry(outcome=AttemptOutcome.SUCCESS, attempt=1, policy=policy) is False
        )

    def test_fatal_is_never_retried(self) -> None:
        """400 重试就是白烧钱。"""
        policy = RetryPolicy(max_retries=3)
        assert (
            should_retry(outcome=AttemptOutcome.FATAL, attempt=1, policy=policy) is False
        )

    def test_schema_invalid_is_not_retried(self) -> None:
        """schema 失败走「修复」流程，不走普通重试。"""
        policy = RetryPolicy(max_retries=3)
        assert (
            should_retry(
                outcome=AttemptOutcome.SCHEMA_INVALID, attempt=1, policy=policy
            )
            is False
        )

    def test_retryable_is_retried_within_budget(self) -> None:
        policy = RetryPolicy(max_retries=2)
        assert (
            should_retry(outcome=AttemptOutcome.RETRYABLE, attempt=1, policy=policy) is True
        )

    def test_exhausted_attempts_not_retried(self) -> None:
        """max_retries=2 时，第 2 次失败后不再重试（总共调用 2 次）。"""
        policy = RetryPolicy(max_retries=2)
        assert (
            should_retry(outcome=AttemptOutcome.RETRYABLE, attempt=2, policy=policy) is True
        )
        assert (
            should_retry(outcome=AttemptOutcome.RETRYABLE, attempt=3, policy=policy) is False
        )

    def test_zero_max_retries_means_single_attempt(self) -> None:
        policy = RetryPolicy(max_retries=0)
        assert (
            should_retry(outcome=AttemptOutcome.RETRYABLE, attempt=1, policy=policy) is False
        )

    def test_deadline_in_the_past_blocks_retry(self) -> None:
        """总预算已耗尽时不要再重试——重试只会让任务整体超时。"""
        policy = RetryPolicy(max_retries=3)
        past = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=10)
        assert (
            should_retry(
                outcome=AttemptOutcome.RETRYABLE,
                attempt=1,
                policy=policy,
                deadline=past,
            )
            is False
        )

    def test_deadline_with_enough_room_allows_retry(self) -> None:
        policy = RetryPolicy(max_retries=3)
        future = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=60)
        assert (
            should_retry(
                outcome=AttemptOutcome.RETRYABLE,
                attempt=1,
                policy=policy,
                deadline=future,
            )
            is True
        )

    def test_deadline_too_close_blocks_retry(self) -> None:
        """剩余时间不够覆盖退避等待时，不该重试。"""
        policy = RetryPolicy(max_retries=3, backoff_base_seconds=10.0)
        # 只剩 1 秒，但退避要等 10 秒
        tight = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=1)
        assert (
            should_retry(
                outcome=AttemptOutcome.RETRYABLE,
                attempt=1,
                policy=policy,
                deadline=tight,
            )
            is False
        )


class TestBuildRepairMessages:
    """schema 修复的提示词构造。"""

    def test_contains_original_output_and_error(self) -> None:
        messages = build_repair_messages(
            system_template_content="SYSTEM",
            original_output='{"summary": "x"}',
            error_summary="- summary: missing",
        )
        assert len(messages) == 2
        assert messages[0] == {"role": "system", "content": "SYSTEM"}
        user = messages[1]["content"]
        assert '{"summary": "x"}' in user
        assert "- summary: missing" in user

    def test_does_not_resend_the_snapshot(self) -> None:
        """**不重传原始上下文**（PRD v2.3 / 成本优化 O12）。

        重传快照会让每次 schema 失败的成本翻倍以上，
        且模型可能「重新分析一遍」给出不同结论，破坏可复现性。
        """
        messages = build_repair_messages(
            system_template_content="SYSTEM",
            original_output="OUT",
            error_summary="ERR",
        )
        user = messages[1]["content"]
        assert "snapshot" not in user.lower()
        assert "{snapshot}" not in user

    def test_asks_to_fix_not_reanalyze(self) -> None:
        """提示词要明确「只修正，不重新分析」。"""
        messages = build_repair_messages(
            system_template_content="SYSTEM",
            original_output="OUT",
            error_summary="ERR",
        )
        user = messages[1]["content"]
        assert "只修正" in user
        assert "不要重新分析" in user


class TestSummarizeValidationIssues:
    """校验错误摘要。"""

    def test_includes_path_and_type(self) -> None:
        text = summarize_validation_issues(
            [{"loc": ["scenarios", 0, "probability"], "type": "missing"}]
        )
        assert "scenarios.0.probability" in text
        assert "missing" in text

    def test_respects_limit(self) -> None:
        issues = [{"loc": [f"f{i}"], "type": "missing"} for i in range(50)]
        text = summarize_validation_issues(issues, limit=3)
        assert len(text.strip().splitlines()) == 3

    def test_empty_loc_falls_back_to_root(self) -> None:
        text = summarize_validation_issues([{"loc": [], "type": "invalid"}])
        assert "(根)" in text


class TestInputTokenLimit:
    """输入 token 护栏。"""

    def test_under_limit_passes(self) -> None:
        assert exceeds_input_token_limit(input_tokens=1000, limit=24000) is False

    def test_over_limit_blocked(self) -> None:
        assert exceeds_input_token_limit(input_tokens=30000, limit=24000) is True

    def test_exactly_at_limit_passes(self) -> None:
        """等于上限不算超。"""
        assert exceeds_input_token_limit(input_tokens=24000, limit=24000) is False

    def test_zero_limit_disables_guard(self) -> None:
        """limit<=0 表示关闭护栏。"""
        assert exceeds_input_token_limit(input_tokens=999999, limit=0) is False

    def test_error_carries_actual_and_limit(self) -> None:
        exc = build_input_limit_error(actual=30000, limit=24000)
        assert isinstance(exc, AICryptoError)
        assert exc.code == ErrorCode.LLM_BUDGET_EXCEEDED
        assert exc.details["actual"] == 30000
        assert exc.details["limit"] == 24000
