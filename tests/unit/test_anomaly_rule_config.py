"""异动规则配置加载的测试（PRD 22.2 / 32.5）。

**背景**：``config/base.toml`` 的 ``[anomaly.rules.*]`` 各段此前**没有任何代码读取**
——规则用的是 ``domain/anomaly/rules.py`` 里的硬编码默认值。
这意味着「配置化」是假的：改 ``base.toml`` 不会改变行为。

这些测试锁死：

1. 配置里的阈值**真的被读出来**；
2. **缺项回落默认值**（配置缺失不该让扫描崩溃）；
3. **类型错误必须报错**——静默把 ``"3.0"`` 当 0.0 会让阈值失效，比崩溃更危险。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicrypto.config.anomaly_rules import (
    AnomalyRuleConfig,
    DerivativesThresholds,
    load_anomaly_rule_config,
)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "base.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 真实项目配置
# --------------------------------------------------------------------------


def test_project_config_loads() -> None:
    """项目自带的 ``base.toml`` 能被加载。"""
    config = load_anomaly_rule_config()
    assert config.rule_version == "v1"
    assert config.derivatives.oi_change_warn_pct == 3.0
    assert config.derivatives.oi_change_critical_pct == 6.0
    assert config.derivatives.funding_abs_warn == 0.0005
    assert config.derivatives.funding_abs_critical == 0.001


def test_project_config_has_disabled_1m_rules() -> None:
    """1m 依赖规则必须在配置里被显式禁用（29.2.1）。"""
    config = load_anomaly_rule_config()
    assert "PRICE_PCT_1M" in config.disabled_rules
    assert "PRICE_GAP" in config.disabled_rules
    assert "PRICE_FLASH_MOVE" in config.disabled_rules


def test_project_config_has_derivatives_thresholds() -> None:
    """衍生品阈值段必须存在且被读到。"""
    config = load_anomaly_rule_config()
    assert config.derivatives.divergence_pct == 1.0
    assert config.derivatives.basis_percentile == 95.0
    assert config.derivatives.ls_skew_percentile == 95.0
    assert config.derivatives.oi_window_seconds == 3600
    assert config.derivatives.funding_stale_seconds == 120.0


# --------------------------------------------------------------------------
# 缺失与回落
# --------------------------------------------------------------------------


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    """文件不存在时回落到默认值（默认值与 PRD 22.2 一致）。"""
    config = load_anomaly_rule_config(tmp_path / "nope.toml")
    assert config.rule_version == "v1"
    assert config.derivatives == DerivativesThresholds()
    assert config.price_move is None


def test_missing_sections_fall_back_to_defaults(tmp_path: Path) -> None:
    """段缺失时该段回落默认值，其余段照常读取。"""
    path = _write(
        tmp_path,
        """
[anomaly.rules]
rule_version = "v3"

[anomaly.rules.derivatives]
oi_change_warn_pct = 5.0
oi_change_critical_pct = 10.0
funding_abs_warn = 0.001
funding_abs_critical = 0.002
""",
    )
    config = load_anomaly_rule_config(path)
    assert config.rule_version == "v3"
    assert config.derivatives.oi_change_warn_pct == 5.0
    # 未给出的项回落默认
    assert config.derivatives.divergence_pct == 1.0
    # 未给出的段为 None
    assert config.price_move is None


def test_empty_derivatives_table_uses_defaults(tmp_path: Path) -> None:
    """空段回落默认。"""
    path = _write(
        tmp_path,
        """
[anomaly.rules]
rule_version = "v1"

[anomaly.rules.derivatives]
""",
    )
    config = load_anomaly_rule_config(path)
    assert config.derivatives == DerivativesThresholds()


# --------------------------------------------------------------------------
# 类型错误必须报错
# --------------------------------------------------------------------------


def test_wrong_type_raises(tmp_path: Path) -> None:
    """阈值写成字符串必须报错——静默当 0 会让阈值失效。"""
    path = _write(
        tmp_path,
        """
[anomaly.rules]
rule_version = "v1"

[anomaly.rules.derivatives]
oi_change_warn_pct = "3.0"
oi_change_critical_pct = 6.0
funding_abs_warn = 0.0005
funding_abs_critical = 0.001
""",
    )
    with pytest.raises(ValueError, match="oi_change_warn_pct"):
        load_anomaly_rule_config(path)


def test_boolean_is_not_a_number(tmp_path: Path) -> None:
    """布尔值不是数值——必须报错。"""
    path = _write(
        tmp_path,
        """
[anomaly.rules]
rule_version = "v1"

[anomaly.rules.derivatives]
oi_change_warn_pct = true
oi_change_critical_pct = 6.0
funding_abs_warn = 0.0005
funding_abs_critical = 0.001
""",
    )
    with pytest.raises(ValueError, match="oi_change_warn_pct"):
        load_anomaly_rule_config(path)


def test_missing_required_key_raises(tmp_path: Path) -> None:
    """衍生品段缺少必填项必须报错，而不是静默用 0。"""
    path = _write(
        tmp_path,
        """
[anomaly.rules]
rule_version = "v1"

[anomaly.rules.derivatives]
oi_change_warn_pct = 3.0
""",
    )
    with pytest.raises(ValueError, match="oi_change_critical_pct"):
        load_anomaly_rule_config(path)


def test_invalid_disabled_rules_type_raises(tmp_path: Path) -> None:
    """``disabled_rules`` 必须是字符串数组。"""
    path = _write(
        tmp_path,
        """
[anomaly.rules]
rule_version = "v1"
disabled_rules = [1, 2]
""",
    )
    with pytest.raises(ValueError, match="disabled_rules"):
        load_anomaly_rule_config(path)


# --------------------------------------------------------------------------
# 完整性
# --------------------------------------------------------------------------


def test_all_derivatives_thresholds_loaded_from_config(tmp_path: Path) -> None:
    """配置加载器必须覆盖 ``DerivativesThresholds`` 的全部字段。"""
    import dataclasses

    path = _write(
        tmp_path,
        """
[anomaly.rules]
rule_version = "v1"

[anomaly.rules.derivatives]
oi_change_warn_pct = 1.0
oi_change_critical_pct = 2.0
funding_abs_warn = 0.001
funding_abs_critical = 0.002
divergence_pct = 3.0
basis_percentile = 90.0
ls_skew_percentile = 88.0
oi_window_seconds = 1800
funding_stale_seconds = 90.0
""",
    )
    config = load_anomaly_rule_config(path)
    assert config.derivatives == DerivativesThresholds(
        oi_change_warn_pct=1.0,
        oi_change_critical_pct=2.0,
        funding_abs_warn=0.001,
        funding_abs_critical=0.002,
        divergence_pct=3.0,
        basis_percentile=90.0,
        ls_skew_percentile=88.0,
        oi_window_seconds=1800,
        funding_stale_seconds=90.0,
    )
    # 确认没有遗漏字段
    assert set(dataclasses.asdict(config.derivatives)) == {
        "oi_change_warn_pct",
        "oi_change_critical_pct",
        "funding_abs_warn",
        "funding_abs_critical",
        "divergence_pct",
        "basis_percentile",
        "ls_skew_percentile",
        "oi_window_seconds",
        "funding_stale_seconds",
    }


def test_default_config_object_is_constructible() -> None:
    """默认构造不报错。"""
    config = AnomalyRuleConfig()
    assert config.rule_version == "v1"
    assert config.disabled_rules == ()
