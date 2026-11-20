"""行情标的解析（PRD 32.1 / 32.2）。

优先级（低 → 高）::

    Settings 代码默认值 → config/base.toml [market].symbols → 环境变量 / .env MARKET_SYMBOLS

标的列表是非敏感配置，允许被环境变量覆盖；仅当 ``MARKET_SYMBOLS`` 被**显式设置**
（``model_fields_set``）时才覆盖 base.toml，避免把代码默认值误判为用户配置。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from aicrypto.config.settings import CONFIG_DIR, Settings

BASE_CONFIG_FILENAME = "base.toml"


def _normalize(items: list[str]) -> list[str]:
    """两条来源共用的规范化：去空、大写、按原顺序去重。"""
    seen: list[str] = []
    for item in items:
        symbol = item.strip().upper()
        if symbol and symbol not in seen:
            seen.append(symbol)
    return seen


def _parse_csv(value: str) -> list[str]:
    return _normalize(value.split(","))


def read_base_symbols(path: Path | None = None) -> list[str] | None:
    """读取 ``base.toml`` 的 ``[market].symbols``；文件或键缺失时返回 None。"""
    target = path or (CONFIG_DIR / BASE_CONFIG_FILENAME)
    if not target.exists():
        return None
    with target.open("rb") as fh:
        data = tomllib.load(fh)
    raw = data.get("market", {}).get("symbols")
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
        raise ValueError(f"{target} [market].symbols 必须是字符串数组")
    symbols = _normalize(raw)
    return symbols or None


def resolve_market_symbols(settings: Settings, base_path: Path | None = None) -> list[str]:
    """按优先级解析标的列表，结果去重、大写、保持原顺序。

    环境变量显式设置则直接采用；否则 base.toml；都没有则回落到 Settings 默认值。
    """
    if "MARKET_SYMBOLS" in settings.model_fields_set:
        symbols = _parse_csv(settings.MARKET_SYMBOLS)
    else:
        from_base = read_base_symbols(base_path)
        symbols = from_base if from_base else _parse_csv(settings.MARKET_SYMBOLS)

    if not symbols:
        raise ValueError("行情标的列表为空：请设置 MARKET_SYMBOLS 或 base.toml [market].symbols")
    return symbols


__all__ = ["read_base_symbols", "resolve_market_symbols"]
