"""行情标的解析优先级与一致性验证（PRD 32.1）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicrypto.config.market_symbols import read_base_symbols, resolve_market_symbols
from aicrypto.config.settings import Settings


def _settings(**overrides: str) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test",
        REDIS_URL="redis://test",
        AUTH_ENABLED=False,
        **overrides,
    )


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "base.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_read_base_symbols_from_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "[market]\nsymbols = ['BTCUSDT', 'ETHUSDT']\n")
    assert read_base_symbols(path) == ["BTCUSDT", "ETHUSDT"]


def test_read_base_symbols_missing_file(tmp_path: Path) -> None:
    assert read_base_symbols(tmp_path / "absent.toml") is None


def test_read_base_symbols_missing_key(tmp_path: Path) -> None:
    path = _write(tmp_path, "[other]\nkey = 'value'\n")
    assert read_base_symbols(path) is None


def test_read_base_symbols_normalization(tmp_path: Path) -> None:
    path = _write(tmp_path, "[market]\nsymbols = ['btcusdt', 'ETHUSDT', '  ', 'btcusdt']\n")
    assert read_base_symbols(path) == ["BTCUSDT", "ETHUSDT"]


def test_read_base_symbols_rejects_non_list(tmp_path: Path) -> None:
    path = _write(tmp_path, "[market]\nsymbols = 'BTCUSDT'\n")
    with pytest.raises(ValueError, match="字符串数组"):
        read_base_symbols(path)


def test_env_override_beats_base_toml(tmp_path: Path) -> None:
    path = _write(tmp_path, "[market]\nsymbols = ['BTCUSDT']\n")
    settings = _settings(MARKET_SYMBOLS="ETHUSDT,BNBUSDT")
    assert resolve_market_symbols(settings, path) == ["ETHUSDT", "BNBUSDT"]


def test_base_toml_used_when_env_not_set(tmp_path: Path) -> None:
    path = _write(tmp_path, "[market]\nsymbols = ['SOLUSDT', 'ADAUSDT']\n")
    assert resolve_market_symbols(_settings(), path) == ["SOLUSDT", "ADAUSDT"]


def test_settings_default_when_no_sources(tmp_path: Path) -> None:
    assert resolve_market_symbols(_settings(), tmp_path / "absent.toml") == [
        "BTCUSDT",
        "ETHUSDT",
    ]


def test_explicit_empty_env_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "[market]\nsymbols = ['BTCUSDT']\n")
    with pytest.raises(ValueError, match="标的列表为空"):
        resolve_market_symbols(_settings(MARKET_SYMBOLS=""), path)


def test_env_deduplicates_and_uppercases(tmp_path: Path) -> None:
    settings = _settings(MARKET_SYMBOLS="BTCUSDT,btcusdt,ETHUSDT,ethusdt")
    assert resolve_market_symbols(settings, tmp_path / "absent.toml") == ["BTCUSDT", "ETHUSDT"]
