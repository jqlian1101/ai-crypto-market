"""配置校验与安全基线测试（PRD 第 32.3 / 34.2 / 34.5）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aicrypto.config.settings import Settings


def base_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "DATABASE_URL": "postgresql+psycopg://user:pass@localhost:5432/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "AUTH_ENABLED": False,
    }
    kwargs.update(overrides)
    return kwargs


class TestStartupBlocking:
    """32.3：校验失败必须阻止进程启动。"""

    def test_missing_database_url_blocks_startup(self) -> None:
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            Settings(REDIS_URL="redis://localhost:6379/0", AUTH_ENABLED=False)

    def test_missing_redis_url_blocks_startup(self) -> None:
        with pytest.raises(ValidationError, match="REDIS_URL"):
            Settings(
                DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/db",
                AUTH_ENABLED=False,
            )

    def test_missing_auth_token_blocks_startup(self) -> None:
        """34.2：``AUTH_TOKEN`` 缺失时拒绝启动。"""
        with pytest.raises(ValidationError, match="AUTH_TOKEN"):
            Settings(**base_kwargs(AUTH_ENABLED=True))

    def test_auth_token_sha256_is_accepted(self) -> None:
        settings = Settings(**base_kwargs(AUTH_ENABLED=True, AUTH_TOKEN_SHA256="a" * 64))
        assert settings.AUTH_ENABLED is True

    def test_public_callback_requires_token(self) -> None:
        with pytest.raises(ValidationError, match="PUBLIC_CALLBACK_TOKEN"):
            Settings(**base_kwargs(PUBLIC_CALLBACK_ENABLED=True))

    def test_feishu_enabled_requires_webhook(self) -> None:
        with pytest.raises(ValidationError, match="FEISHU_WEBHOOK_URL"):
            Settings(**base_kwargs(FEISHU_ENABLED=True))

    def test_wecom_app_requires_credentials(self) -> None:
        with pytest.raises(ValidationError, match="WECOM"):
            Settings(**base_kwargs(WECOM_APP_ENABLED=True))

    def test_freqtrade_requires_dry_run(self) -> None:
        with pytest.raises(ValidationError, match="FREQTRADE_DRY_RUN_ENFORCE"):
            Settings(**base_kwargs(FREQTRADE_ENABLED=True, FREQTRADE_DRY_RUN_ENFORCE=False))

    def test_freqtrade_image_tag_cannot_be_latest(self) -> None:
        with pytest.raises(ValidationError, match="FREQTRADE_IMAGE_TAG"):
            Settings(**base_kwargs(FREQTRADE_ENABLED=True, FREQTRADE_IMAGE_TAG="latest"))


class TestNetworkExposure:
    """34.5：暴露到非回环且无令牌时拒绝启动。"""

    def test_non_loopback_without_auth_rejected(self) -> None:
        with pytest.raises(ValidationError, match="非回环"):
            Settings(**base_kwargs(APP_HOST="0.0.0.0", AUTH_ENABLED=False))

    def test_non_loopback_with_auth_allowed(self) -> None:
        settings = Settings(
            **base_kwargs(APP_HOST="0.0.0.0", AUTH_ENABLED=True, AUTH_TOKEN="x" * 32)
        )
        assert settings.is_loopback_only is False


class TestFormatValidation:
    """32.3：格式校验在启动时进行。"""

    def test_invalid_quiet_hours_rejected(self) -> None:
        with pytest.raises(ValidationError, match="NOTIFY_QUIET_HOURS"):
            Settings(**base_kwargs(NOTIFY_QUIET_HOURS="25:00-07:00"))

    def test_valid_quiet_hours_accepted(self) -> None:
        settings = Settings(**base_kwargs(NOTIFY_QUIET_HOURS="00:00-07:00"))
        assert settings.NOTIFY_QUIET_HOURS == "00:00-07:00"

    def test_invalid_retry_backoff_rejected(self) -> None:
        with pytest.raises(ValidationError, match="NOTIFY_RETRY_BACKOFF"):
            Settings(**base_kwargs(NOTIFY_RETRY_BACKOFF="1,3,10"))

    def test_llm_token_bounds_checked(self) -> None:
        with pytest.raises(ValidationError, match="LLM_MAX_OUTPUT_TOKENS"):
            Settings(**base_kwargs(LLM_MAX_OUTPUT_TOKENS=8000, LLM_AGENT_MAX_TOKENS=4000))


class TestDerivedProperties:
    def test_symbols_parsing(self) -> None:
        settings = Settings(**base_kwargs(MARKET_SYMBOLS="btcusdt, ethusdt"))
        assert settings.symbols == ["BTCUSDT", "ETHUSDT"]

    def test_timeframes_includes_1m_when_enabled(self) -> None:
        settings = Settings(**base_kwargs(MARKET_ENABLED_INTERVALS_1M=True))
        assert "1m" in [str(t) for t in settings.timeframes]

    def test_severity_routing_parsing(self) -> None:
        settings = Settings(**base_kwargs(NOTIFY_SEVERITY_ROUTING="CRITICAL:wecom_app,INFO:feishu"))
        assert settings.severity_routing == {
            "CRITICAL": ["wecom_app"],
            "INFO": ["feishu"],
        }

    def test_retry_backoff_parsing(self) -> None:
        settings = Settings(**base_kwargs())
        assert settings.retry_backoff_seconds == [1, 3, 10, 30]

    def test_redacted_summary_masks_secrets(self) -> None:
        settings = Settings(**base_kwargs(AUTH_ENABLED=True, AUTH_TOKEN="super-secret"))
        summary = settings.redacted_summary()
        assert summary["AUTH_TOKEN"] == "***"
        assert "super-secret" not in str(summary)

    def test_config_hash_is_stable(self) -> None:
        a = Settings(**base_kwargs())
        b = Settings(**base_kwargs())
        assert a.config_hash() == b.config_hash()
