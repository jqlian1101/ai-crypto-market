"""异动规则配置的读取与阈值对象装配（PRD 22.2 / 32.5）。

**为什么需要这一层**：``config/base.toml`` 的 ``[anomaly.rules.*]`` 各段
（``price_move`` / ``volatility`` / ``volume`` / ``derivatives`` /
``data_quality``）此前**没有任何代码读取**——规则用的是
``domain/anomaly/rules.py`` 里的 dataclass 硬编码默认值。
这意味着改 ``base.toml`` 不会改变行为，「配置化」是假的。

本模块建立完整链路：

    config/base.toml  ->  阈值 dataclass  ->  规则引擎

**阈值与版本的关系**：``base.toml`` 注释明确要求「修改任一阈值必须递增
``rule_version``」。本模块读取 ``rule_version`` 并交给调用方落库到
``anomaly_rules`` 表（PRD 30.6：改阈值不改已有规则行，新增版本）。

**缺省行为**：文件或某段缺失时**回落到 dataclass 默认值**——
默认值与 PRD 22.2 初始值一致，配置缺失不应让扫描直接崩溃。
但**类型错误必须报错**：静默把 ``"3.0"`` 当 0.0 会让阈值失效，
比崩溃更危险。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aicrypto.config.settings import CONFIG_DIR

BASE_CONFIG_FILENAME = "base.toml"


@dataclass(frozen=True, slots=True)
class DerivativesThresholds:
    """衍生品异动阈值（PRD 22.2.4）。

    ``oi_change_warn_pct`` / ``oi_change_critical_pct`` 是**百分比**（3.0 表示 3%）。
    ``funding_abs_warn`` / ``funding_abs_critical`` 是**小数**（0.0005 表示 0.05%）。
    """

    oi_change_warn_pct: float = 3.0
    oi_change_critical_pct: float = 6.0
    funding_abs_warn: float = 0.0005
    funding_abs_critical: float = 0.001
    #: 价格/OI 背离阈值（百分比）。两者的反向变动都需超过此值。
    divergence_pct: float = 1.0
    #: 基差极端的分位阈值（PRD 22.2.4：|basis| 处于历史 95 分位以上）
    basis_percentile: float = 95.0
    #: 多空拥挤的分位阈值（PRD 22.2.4：≥P95 或 ≤P5）
    ls_skew_percentile: float = 95.0
    #: OI 变化率窗口（秒）。1h 与 PRD 22.2.4 一致。
    oi_window_seconds: int = 3600
    #: 资金费率陈旧阈值（秒）。超过则不参与判定（29.4.3 通路 C）。
    funding_stale_seconds: float = 120.0


@dataclass(frozen=True, slots=True)
class AnomalyRuleConfig:
    """从 ``base.toml`` 读出的全部异动规则配置。"""

    rule_version: str = "v1"
    disabled_rules: tuple[str, ...] = ()
    price_move: dict[str, float] | None = None
    volatility: dict[str, float] | None = None
    volume: dict[str, float] | None = None
    derivatives: DerivativesThresholds = DerivativesThresholds()
    data_quality: dict[str, float] | None = None


def _require_number(table: dict[str, Any], key: str, source: Path) -> float:
    """读取一个数值配置项。类型不对必须报错，不静默回落。"""
    if key not in table:
        raise ValueError(f"{source} 缺少配置项 {key!r}")
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{source} 的 {key!r} 必须是数值，实际是 {type(value).__name__}")
    return float(value)


def _read_table(data: dict[str, Any], section: str, source: Path) -> dict[str, Any]:
    rules = data.get("anomaly", {})
    if not isinstance(rules, dict):
        raise ValueError(f"{source} 的 [anomaly] 必须是表")
    table = rules.get("rules", {})
    if not isinstance(table, dict):
        raise ValueError(f"{source} 的 [anomaly.rules] 必须是表")
    value = table.get(section, {})
    if not isinstance(value, dict):
        raise ValueError(f"{source} 的 [anomaly.rules.{section}] 必须是表")
    return value


def load_anomaly_rule_config(path: Path | None = None) -> AnomalyRuleConfig:
    """加载异动规则配置。

    文件不存在时返回**全默认值**的配置（默认值与 PRD 22.2 初始值一致），
    保证配置缺失不会让扫描崩溃。
    """
    target = path or (CONFIG_DIR / BASE_CONFIG_FILENAME)
    if not target.exists():
        return AnomalyRuleConfig()
    with target.open("rb") as fh:
        data = tomllib.load(fh)

    rules = data.get("anomaly", {}).get("rules", {})
    if not isinstance(rules, dict):
        raise ValueError(f"{target} 的 [anomaly.rules] 必须是表")

    version = str(rules.get("rule_version", "v1"))
    disabled_raw = rules.get("disabled_rules", [])
    if not isinstance(disabled_raw, list) or not all(
        isinstance(item, str) for item in disabled_raw
    ):
        raise ValueError(f"{target} 的 disabled_rules 必须是字符串数组")
    disabled = tuple(item.strip() for item in disabled_raw if item.strip())

    price_move: dict[str, float] | None = None
    price_table = _read_table(data, "price_move", target)
    if price_table:
        price_move = {
            key: _require_number(price_table, key, target)
            for key in (
                "5m_warn_pct",
                "5m_critical_pct",
                "15m_warn_pct",
                "15m_critical_pct",
                "1h_warn_pct",
                "1h_critical_pct",
            )
        }

    volatility: dict[str, float] | None = None
    volatility_table = _read_table(data, "volatility", target)
    if volatility_table:
        volatility = {
            key: _require_number(volatility_table, key, target)
            for key in ("atr_ratio_warn", "atr_ratio_critical", "atr_baseline_bars")
        }

    volume: dict[str, float] | None = None
    volume_table = _read_table(data, "volume", target)
    if volume_table:
        volume = {
            key: _require_number(volume_table, key, target)
            for key in ("volume_ratio_warn", "volume_ratio_critical", "volume_lookback")
        }

    data_quality: dict[str, float] | None = None
    dq_table = _read_table(data, "data_quality", target)
    if dq_table:
        data_quality = {
            key: _require_number(dq_table, key, target)
            for key in (
                "gap_warn_bars",
                "gap_critical_bars",
                "stale_warn_seconds",
                "stale_critical_seconds",
            )
        }

    derivatives = DerivativesThresholds()
    der_table = _read_table(data, "derivatives", target)
    if der_table:
        defaults = DerivativesThresholds()
        derivatives = DerivativesThresholds(
            oi_change_warn_pct=_require_number(der_table, "oi_change_warn_pct", target),
            oi_change_critical_pct=_require_number(der_table, "oi_change_critical_pct", target),
            funding_abs_warn=_require_number(der_table, "funding_abs_warn", target),
            funding_abs_critical=_require_number(der_table, "funding_abs_critical", target),
            divergence_pct=_require_number(der_table, "divergence_pct", target)
            if "divergence_pct" in der_table
            else defaults.divergence_pct,
            basis_percentile=_require_number(der_table, "basis_percentile", target)
            if "basis_percentile" in der_table
            else defaults.basis_percentile,
            ls_skew_percentile=_require_number(der_table, "ls_skew_percentile", target)
            if "ls_skew_percentile" in der_table
            else defaults.ls_skew_percentile,
            oi_window_seconds=int(_require_number(der_table, "oi_window_seconds", target))
            if "oi_window_seconds" in der_table
            else defaults.oi_window_seconds,
            funding_stale_seconds=_require_number(der_table, "funding_stale_seconds", target)
            if "funding_stale_seconds" in der_table
            else defaults.funding_stale_seconds,
        )

    return AnomalyRuleConfig(
        rule_version=version,
        disabled_rules=disabled,
        price_move=price_move,
        volatility=volatility,
        volume=volume,
        derivatives=derivatives,
        data_quality=data_quality,
    )


__all__ = [
    "BASE_CONFIG_FILENAME",
    "AnomalyRuleConfig",
    "DerivativesThresholds",
    "load_anomaly_rule_config",
]
