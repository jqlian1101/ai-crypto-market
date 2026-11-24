"""``.env.example`` 模板的可直接使用性（PRD 32.2 / 32.3）。

**背景**：PRD 32.2 要求 ``.env.example`` 对未配置项**留空**（而不是注释掉），
敏感项也必须留空。按模板复制出的 ``.env`` 是新用户的第一步，因此它必须
**能被直接加载**，不能因为「空的可选字段」而阻止进程启动（32.3）。

本测试锁死这条约定：

- 可空字段（``X | None``）留空 → 归一化为 ``None``，**不报错**；
- 必填字段留空 → 仍然报错（真正的配置缺失必须显式暴露）；
- 非空但非法的值 → 仍然报错（不能被「留空即未配置」的归一化掩盖）。
"""

from __future__ import annotations

import tomllib

import pytest
from pydantic import ValidationError

from aicrypto.config.settings import PROJECT_ROOT, Settings

ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"


def _blank_env_example_keys() -> dict[str, str]:
    """从 ``.env.example`` 解析出所有**留空**的配置项。"""
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def test_env_example_has_blank_optional_fields() -> None:
    """模板里确实存在「留空的可选字段」——否则本模块的测试就是空转。"""
    blanks = {k for k, v in _blank_env_example_keys().items() if v == ""}
    assert blanks, ".env.example 应包含留空项（PRD 32.2）"


class TestBlankOptionalFields:
    """留空的可选字段归一化为 ``None``，不阻止启动。"""

    @pytest.mark.parametrize(
        "key",
        [
            "LLM_ENABLE_THINKING",
            "LLM_DAILY_TOKEN_BUDGET",
            "LLM_DAILY_COST_BUDGET_USD",
            "LLM_CLOUD_BASE_URL",
            "LLM_CLOUD_API_KEY",
            "FEISHU_WEBHOOK_URL",
            "WECOM_CORP_ID",
        ],
    )
    def test_blank_optional_field_becomes_none(self, key: str) -> None:
        """每个可选字段留空都应解析为 ``None``，而不是类型错误。"""
        settings = Settings(
            DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/db",
            REDIS_URL="redis://localhost:6379/0",
            AUTH_ENABLED=False,
            **{key: ""},
        )
        assert getattr(settings, key) is None

    def test_blank_optional_does_not_mask_required_fields(self) -> None:
        """留空归一化**不能**掩盖必填字段的缺失。"""
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            Settings(
                DATABASE_URL="",
                REDIS_URL="redis://localhost:6379/0",
                AUTH_ENABLED=False,
            )

    def test_blank_optional_does_not_mask_invalid_values(self) -> None:
        """非空但非法的值仍然必须报错。"""
        with pytest.raises(ValidationError):
            Settings(
                DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/db",
                REDIS_URL="redis://localhost:6379/0",
                AUTH_ENABLED=False,
                LLM_DAILY_TOKEN_BUDGET="not-a-number",
            )

    def test_blank_int_optional_accepts_zero_like_strings(self) -> None:
        """``"0"`` 不是空串，仍按字面量解析（不被误当作未配置）。"""
        settings = Settings(
            DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/db",
            REDIS_URL="redis://localhost:6379/0",
            AUTH_ENABLED=False,
            LLM_MAX_RETRIES="0",
        )
        assert settings.LLM_MAX_RETRIES == 0


class TestEnvExampleIsLoadable:
    """整份模板（仅填必填项）必须能加载成功。"""

    def test_full_template_with_only_required_filled_loads(self) -> None:
        """模拟用户「复制模板 → 只填 DATABASE_URL/REDIS_URL」的首次启动。"""
        values = _blank_env_example_keys()
        values["DATABASE_URL"] = "postgresql+psycopg://u:p@localhost:5432/db"
        values["REDIS_URL"] = "redis://localhost:6379/0"
        values["AUTH_ENABLED"] = "false"

        kwargs: dict[str, object] = {}
        for key, value in values.items():
            if key not in Settings.model_fields:
                continue
            if value == "":
                # 必填项留空同样按「未配置」处理，交由 Settings 校验器决定是否致命
                kwargs[key] = ""
            else:
                kwargs[key] = value

        settings = Settings(**kwargs)
        assert settings.DATABASE_URL
        assert settings.REDIS_URL

    def test_template_keys_match_settings_fields(self) -> None:
        """模板键必须都是真实的 Settings 字段（避免模板里出现僵尸配置）。"""
        keys = set(_blank_env_example_keys())
        unknown = sorted(keys - set(Settings.model_fields))
        assert not unknown, f".env.example 含未知配置项: {unknown}"


def test_base_toml_derivatives_section_is_readable() -> None:
    """``config/base.toml`` 必须能被解析（含衍生品阈值段）。"""
    with (PROJECT_ROOT / "config" / "base.toml").open("rb") as fh:
        data = tomllib.load(fh)
    rules = data["anomaly"]["rules"]
    assert "derivatives" in rules, "衍生品规则阈值段缺失（PRD 22.2.4）"
