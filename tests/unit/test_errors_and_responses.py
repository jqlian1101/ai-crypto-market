"""错误码字典与统一响应测试（PRD 第 31.2 / 31.6）。"""

from __future__ import annotations

import pytest

from aicrypto.common.errors import (
    AICryptoError,
    AuthenticationError,
    ErrorCode,
    ERROR_CATALOG,
    ConfigurationError,
)
from aicrypto.common.pagination import (
    build_pagination_meta,
    decode_cursor,
    encode_cursor,
)
from aicrypto.common.responses import success_payload


class TestErrorCatalog:
    def test_every_code_has_a_spec(self) -> None:
        for code in ErrorCode:
            assert code in ERROR_CATALOG, f"{code} 未登记到 ERROR_CATALOG"

    def test_code_format(self) -> None:
        """错误码格式：``{DOMAIN}_{REASON}``，全大写，下划线分隔（31.6）。"""
        for code in ErrorCode:
            text = str(code)
            assert text.isupper()
            assert "_" in text
            assert " " not in text
            assert all(part.replace("_", "").isalnum() for part in text.split("_"))

    def test_retryable_codes(self) -> None:
        assert ERROR_CATALOG[ErrorCode.COMMON_RATE_LIMITED].retryable is True
        assert ERROR_CATALOG[ErrorCode.LLM_UNAVAILABLE].retryable is True
        assert ERROR_CATALOG[ErrorCode.COMMON_UNAUTHORIZED].retryable is False

    def test_http_status_matches_prd(self) -> None:
        assert ERROR_CATALOG[ErrorCode.COMMON_UNAUTHORIZED].http_status == 401
        assert ERROR_CATALOG[ErrorCode.COMMON_VALIDATION_FAILED].http_status == 422
        assert ERROR_CATALOG[ErrorCode.COMMON_NOT_FOUND].http_status == 404
        assert ERROR_CATALOG[ErrorCode.COMMON_CONFLICT].http_status == 409
        assert ERROR_CATALOG[ErrorCode.COMMON_RATE_LIMITED].http_status == 429
        assert ERROR_CATALOG[ErrorCode.COMMON_INTERNAL_ERROR].http_status == 500
        assert ERROR_CATALOG[ErrorCode.COMMON_SERVICE_UNAVAILABLE].http_status == 503

    def test_notify_deduplicated_is_not_an_error(self) -> None:
        """31.6：``NOTIFY_DEDUPLICATED`` 是 200，非错误。"""
        assert ERROR_CATALOG[ErrorCode.NOTIFY_DEDUPLICATED].http_status == 200

    def test_eval_insufficient_sample_is_not_an_error(self) -> None:
        assert ERROR_CATALOG[ErrorCode.EVAL_INSUFFICIENT_SAMPLE].http_status == 200


class TestErrorPayload:
    def test_payload_shape(self) -> None:
        error = AICryptoError(ErrorCode.DATA_QUALITY_STALE, details={"symbol": "BTCUSDT"})
        payload = error.to_payload("req_123")
        assert payload["error"]["code"] == "DATA_QUALITY_STALE"
        assert payload["error"]["request_id"] == "req_123"
        assert payload["error"]["retryable"] is False
        assert payload["error"]["details"] == {"symbol": "BTCUSDT"}

    def test_authentication_error_code(self) -> None:
        error = AuthenticationError()
        assert error.code == ErrorCode.COMMON_UNAUTHORIZED
        assert error.http_status == 401

    def test_unregistered_code_raises(self) -> None:
        with pytest.raises(ValueError, match="未登记的错误码"):
            AICryptoError("NOT_A_REAL_CODE")  # type: ignore[arg-type]

    def test_configuration_error(self) -> None:
        error = ConfigurationError("缺少 DATABASE_URL")
        assert "DATABASE_URL" in error.message


class TestSuccessPayload:
    def test_shape(self) -> None:
        import datetime as dt

        payload = success_payload(
            {"status": "ok"},
            request_id="req_1",
            trace_id="tr_1",
            generated_at=dt.datetime(2026, 11, 12, tzinfo=dt.timezone.utc),
        )
        assert payload["data"] == {"status": "ok"}
        assert payload["meta"]["request_id"] == "req_1"
        assert payload["meta"]["trace_id"] == "tr_1"


class TestPagination:
    def test_cursor_roundtrip(self) -> None:
        payload = {"created_at": "2026-11-12T00:00:00Z", "id": "abc"}
        cursor = encode_cursor(payload)
        assert decode_cursor(cursor) == payload

    def test_cursor_is_opaque(self) -> None:
        cursor = encode_cursor({"id": "abc"})
        assert "abc" not in cursor

    def test_invalid_cursor_raises(self) -> None:
        with pytest.raises(ValueError):
            decode_cursor("!!!not-base64!!!")

    def test_next_cursor_omitted_when_no_more(self) -> None:
        """31.3 规则 3：``has_more=false`` 时不再返回 ``next_cursor``。"""
        meta = build_pagination_meta(
            limit=50, offset=0, total=100, has_more=False, next_cursor="abc"
        )
        assert "next_cursor" not in meta
        assert meta["has_more"] is False

    def test_next_cursor_present_when_more(self) -> None:
        meta = build_pagination_meta(
            limit=50, offset=0, total=100, has_more=True, next_cursor="abc"
        )
        assert meta["next_cursor"] == "abc"
