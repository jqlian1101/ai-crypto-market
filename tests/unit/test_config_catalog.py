"""配置目录一致性校验（PRD 第 32.5 节 / 36.4 ``config-check`` job）。

要求：任何新配置项必须同时出现在

1. ``config/base.toml``（非敏感）或 ``.env.example``（敏感）；
2. PRD 第 32.4 章对应小节的表格（人工维护，本测试无法自动校验）；
3. 代码中的 Pydantic Settings 模型。

本测试校验「Settings 模型 ↔ config/catalog.toml ↔ .env.example」三者一致。
"""

from __future__ import annotations

import pytest

from aicrypto.config.catalog import check_catalog_consistency, load_catalog
from aicrypto.config.settings import PROJECT_ROOT, Settings

CATALOG_PATH = PROJECT_ROOT / "config" / "catalog.toml"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CATALOG_PATH)


def test_catalog_file_exists() -> None:
    assert CATALOG_PATH.exists(), "config/catalog.toml 缺失"


def test_env_example_file_exists() -> None:
    assert ENV_EXAMPLE_PATH.exists(), ".env.example 缺失"


def test_all_settings_fields_registered(catalog) -> None:
    """Settings 的每个字段都必须在配置目录中登记。"""
    problems = check_catalog_consistency(catalog, Settings)
    assert not problems, "配置目录与 Settings 不一致:\n" + "\n".join(problems)


def test_no_extra_entries_in_catalog(catalog) -> None:
    """配置目录不得包含 Settings 中不存在的键（避免僵尸配置）。"""
    from aicrypto.config.catalog import diff_catalog_against_settings

    missing_in_model, missing_in_catalog = diff_catalog_against_settings(catalog, Settings)
    assert not missing_in_model, f"目录中存在无效配置项: {missing_in_model}"
    assert not missing_in_catalog, f"Settings 存在未登记配置项: {missing_in_catalog}"


def test_env_example_contains_all_keys(catalog) -> None:
    """``.env.example`` 必须包含全部配置项（32.2）。"""
    content = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    missing = [key for key in catalog.entries if f"{key}=" not in content]
    assert not missing, f".env.example 缺少配置项: {missing}"


def test_sensitive_keys_have_empty_values_in_env_example(catalog) -> None:
    """敏感项在 ``.env.example`` 中必须留空（32.6）。"""
    lines = ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()

    for key in catalog.sensitive_keys:
        assert key in values, f"敏感项 {key} 未在 .env.example 中出现"
        assert values[key] == "", f"敏感项 {key} 在 .env.example 中不应有值"


def test_security_critical_settings_are_not_in_base_toml() -> None:
    """安全相关项不得出现在结构化配置文件 ``base.toml`` 中（32.1 硬约束）。"""
    base_path = PROJECT_ROOT / "config" / "base.toml"
    content = base_path.read_text(encoding="utf-8")
    security_keys = [
        "AUTH_TOKEN",
        "AUTH_TOKEN_SHA256",
        "PUBLIC_CALLBACK_TOKEN",
        "FEISHU_WEBHOOK_URL",
        "FEISHU_SECRET",
        "WECOM_CORP_SECRET",
        "WECOM_BOT_WEBHOOK_URL",
        "LLM_CLOUD_API_KEY",
        "FREQTRADE_API_PASSWORD",
    ]
    for key in security_keys:
        assert key not in content, f"安全相关项 {key} 不应出现在 config/base.toml"


def test_catalog_is_not_empty(catalog) -> None:
    assert len(catalog) > 50, "配置目录条目过少，疑似未完整登记"
