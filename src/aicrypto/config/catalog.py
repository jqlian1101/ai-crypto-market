"""配置目录加载与一致性校验（PRD 第 32.5 节）。

``config/catalog.toml`` 是**机器可读的配置目录**：键名、类型、默认值、是否敏感、
PRD 章节。CI 通过本模块校验它与 Settings 模型一致（36.4 ``config-check`` job）。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic.fields import FieldInfo

from aicrypto.config.settings import CONFIG_DIR, Settings

CATALOG_FILENAME = "catalog.toml"


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """一个配置项的目录条目。"""

    key: str
    type: str
    default: str | None
    sensitive: bool
    prd_section: str
    group: str


@dataclass(frozen=True, slots=True)
class ConfigCatalog:
    """配置目录。"""

    entries: dict[str, CatalogEntry]

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, key: object) -> bool:
        return key in self.entries

    def __getitem__(self, key: str) -> CatalogEntry:
        return self.entries[key]

    @property
    def sensitive_keys(self) -> set[str]:
        return {k for k, v in self.entries.items() if v.sensitive}


def _flatten(table: dict[str, Any], group: str) -> dict[str, dict[str, Any]]:
    """把 TOML 的分组表扁平化为 ``key -> entry``。"""
    flat: dict[str, dict[str, Any]] = {}
    for key, value in table.items():
        if not isinstance(value, dict):
            continue
        if "type" in value:
            flat[key] = value
            continue
        flat.update(_flatten(value, key))
    return flat


def load_catalog(path: Path | None = None) -> ConfigCatalog:
    """加载 ``config/catalog.toml``。"""
    catalog_path = path or (CONFIG_DIR / CATALOG_FILENAME)
    if not catalog_path.exists():
        raise FileNotFoundError(f"配置目录不存在: {catalog_path}")
    with catalog_path.open("rb") as fh:
        raw = tomllib.load(fh)
    entries: dict[str, CatalogEntry] = {}
    for group, table in raw.items():
        for key, spec in _flatten(table, group).items():
            raw_default = spec.get("default")
            # TOML 没有 null 字面量；目录中用字符串 "null" 表示「无默认值」
            default = None if raw_default == "null" else raw_default
            entries[key] = CatalogEntry(
                key=key,
                type=str(spec["type"]),
                default=default,
                sensitive=bool(spec.get("sensitive", False)),
                prd_section=str(spec.get("prd_section", group)),
                group=group,
            )
    return ConfigCatalog(entries=entries)


def _normalize_type(field: FieldInfo) -> str:
    """把 Pydantic 字段注解归一化为目录中的类型名。"""
    annotation = field.annotation
    text = str(annotation)
    if "SecretStr" in text:
        return "string"
    if "bool" in text:
        return "bool"
    if "int" in text:
        return "int"
    if "float" in text:
        return "float"
    return "string"


def _normalize_default(field: FieldInfo) -> str | None:
    default = field.default
    if default is None:
        return None
    if isinstance(default, SecretStr):
        # SecretStr 的 str() 是掩码值；只有真正配置了明文才算有默认值
        secret = default.get_secret_value()
        return secret if secret else None
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, (int, float, str)):
        return str(default)
    return str(default)


def diff_catalog_against_settings(
    catalog: ConfigCatalog,
    settings_cls: type[Settings] = Settings,
) -> tuple[list[str], list[str]]:
    """比较配置目录与 Settings 模型。

    返回 ``(missing_in_catalog, missing_in_model)``：

    - ``missing_in_catalog``：Settings 中有、目录中缺失的键
    - ``missing_in_model``：目录中有、Settings 中缺失的键
    """
    model_fields = settings_cls.model_fields
    catalog_keys = set(catalog.entries)

    missing_in_catalog = sorted(set(model_fields) - catalog_keys)
    missing_in_model = sorted(catalog_keys - set(model_fields))
    return missing_in_catalog, missing_in_model


def check_catalog_consistency(
    catalog: ConfigCatalog,
    settings_cls: type[Settings] = Settings,
) -> list[str]:
    """返回不一致项的描述列表。空列表表示一致。"""
    problems: list[str] = []
    missing_in_catalog, missing_in_model = diff_catalog_against_settings(catalog, settings_cls)
    for key in missing_in_catalog:
        problems.append(f"Settings 字段 {key} 未登记到 config/catalog.toml")
    for key in missing_in_model:
        problems.append(f"config/catalog.toml 中的 {key} 在 Settings 中不存在")

    for key, entry in catalog.entries.items():
        field = settings_cls.model_fields.get(key)
        if field is None:
            continue
        expected_type = _normalize_type(field)
        if expected_type != entry.type:
            problems.append(
                f"{key} 类型不一致: catalog={entry.type} settings={expected_type}"
            )
        expected_default = _normalize_default(field)
        if expected_default != entry.default:
            problems.append(
                f"{key} 默认值不一致: catalog={entry.default!r} settings={expected_default!r}"
            )
    return problems
