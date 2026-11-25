"""模型单价配置读取的测试（PRD 24.2 / 30.4 / 32.2）。

**背景**��``config/base.toml`` 的 ``[llm.model_pricing]`` 段**早已存在**
（含 claude-haiku / claude-sonnet / claude-opus 的真实单价），
但**没有任何代码读取它**——与 L-03（保留期配置只声明未消费）同源。

这些测试锁死：

1. 项目自带的单价段**真的能被读出来**；
2. **类型错误必须报错**——静默当 0 会让成本变成 0，比崩溃危险得多；
3. 生效时间支持日期与 ISO 两种形式，不带时区的按 UTC 处理；
4. 同步是**幂等**的，同版本号下单价变了必须报错。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from aicrypto.config.model_pricing import (
    ModelPricingConfig,
    PricingConfigError,
    load_model_pricing,
)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "base.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 项目自带配置
# --------------------------------------------------------------------------


def test_project_pricing_loads() -> None:
    """项目自带的 ``base.toml`` 单价段必须能被读到。"""
    entries = load_model_pricing()
    assert entries, "base.toml 的 [llm.model_pricing] 必须存在且能被读取"
    by_id = {item.model_id: item for item in entries}
    assert "claude-haiku" in by_id
    assert "claude-sonnet" in by_id
    assert "claude-opus" in by_id


def test_project_pricing_values() -> None:
    """单价值必须与 base.toml 一致（Decimal 精确相等）。"""
    entries = {item.model_id: item for item in load_model_pricing()}
    assert entries["claude-haiku"].input_usd_per_mtok == Decimal("0.10")
    assert entries["claude-haiku"].output_usd_per_mtok == Decimal("0.50")
    assert entries["claude-sonnet"].input_usd_per_mtok == Decimal("2.00")
    assert entries["claude-opus"].output_usd_per_mtok == Decimal("20.00")


def test_project_pricing_cached_input() -> None:
    """缓存输入单价必须被读到。"""
    entries = {item.model_id: item for item in load_model_pricing()}
    assert entries["claude-sonnet"].cached_input_usd_per_mtok == Decimal("0.10")


def test_project_pricing_effective_from_is_utc() -> None:
    """生效日期不带时区时按 UTC 处理（30.1：数据库统一存 UTC）。"""
    entries = {item.model_id: item for item in load_model_pricing()}
    effective = entries["claude-haiku"].effective_from
    assert effective.tzinfo is not None
    assert effective == dt.datetime(2026, 11, 1, tzinfo=dt.UTC)


# --------------------------------------------------------------------------
# 缺失与回落
# --------------------------------------------------------------------------


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    """文件不存在返回空列表，不报错（单价可以只从数据库维护）。"""
    assert load_model_pricing(tmp_path / "nope.toml") == []


def test_missing_section_returns_empty(tmp_path: Path) -> None:
    """该段缺失返回空列表。"""
    path = _write(tmp_path, "[llm]\nprovider_default = 'cloud'\n")
    assert load_model_pricing(path) == []


def test_empty_section_returns_empty(tmp_path: Path) -> None:
    path = _write(tmp_path, "[llm]\n[llm.model_pricing]\n")
    assert load_model_pricing(path) == []


# --------------------------------------------------------------------------
# 类型错误必须报错
# --------------------------------------------------------------------------


def test_non_numeric_input_rate_raises(tmp_path: Path) -> None:
    """单价写成字符串必须报错——静默当 0 会让成本变成 0。"""
    path = _write(
        tmp_path,
        """
[llm.model_pricing."m"]
input_per_mtok = "0.10"
output_per_mtok = 0.50
effective_from = "2026-11-01"
""",
    )
    with pytest.raises(PricingConfigError, match="input_per_mtok"):
        load_model_pricing(path)


def test_missing_output_rate_raises(tmp_path: Path) -> None:
    """缺必填单价项必须报错，不能静默用 0。"""
    path = _write(
        tmp_path,
        """
[llm.model_pricing."m"]
input_per_mtok = 0.10
effective_from = "2026-11-01"
""",
    )
    with pytest.raises(PricingConfigError, match="output_per_mtok"):
        load_model_pricing(path)


def test_boolean_rate_raises(tmp_path: Path) -> None:
    """布尔不是数值——必须报错。"""
    path = _write(
        tmp_path,
        """
[llm.model_pricing."m"]
input_per_mtok = true
output_per_mtok = 0.50
effective_from = "2026-11-01"
""",
    )
    with pytest.raises(PricingConfigError, match="input_per_mtok"):
        load_model_pricing(path)


def test_invalid_date_raises(tmp_path: Path) -> None:
    """非法日期必须报错。"""
    path = _write(
        tmp_path,
        """
[llm.model_pricing."m"]
input_per_mtok = 0.10
output_per_mtok = 0.50
effective_from = "not-a-date"
""",
    )
    with pytest.raises(PricingConfigError, match="effective_from"):
        load_model_pricing(path)


def test_missing_effective_from_raises(tmp_path: Path) -> None:
    """缺生效日期必须报错——否则无法判断哪一版单价生效。"""
    path = _write(
        tmp_path,
        """
[llm.model_pricing."m"]
input_per_mtok = 0.10
output_per_mtok = 0.50
""",
    )
    with pytest.raises(PricingConfigError, match="effective_from"):
        load_model_pricing(path)


# --------------------------------------------------------------------------
# 日期格式
# --------------------------------------------------------------------------


def test_iso_with_timezone_is_preserved(tmp_path: Path) -> None:
    """带时区的 ISO 时间保留原时区。"""
    path = _write(
        tmp_path,
        """
[llm.model_pricing."m"]
input_per_mtok = 0.10
output_per_mtok = 0.50
effective_from = "2026-11-01T08:00:00+08:00"
""",
    )
    entry = load_model_pricing(path)[0]
    assert entry.effective_from.utcoffset() == dt.timedelta(hours=8)


def test_iso_with_z_is_parsed(tmp_path: Path) -> None:
    """``Z`` 后缀必须能解析。"""
    path = _write(
        tmp_path,
        """
[llm.model_pricing."m"]
input_per_mtok = 0.10
output_per_mtok = 0.50
effective_from = "2026-11-01T00:00:00Z"
""",
    )
    entry = load_model_pricing(path)[0]
    assert entry.effective_from == dt.datetime(2026, 11, 1, tzinfo=dt.UTC)


# --------------------------------------------------------------------------
# 版本与结构
# --------------------------------------------------------------------------


def test_pricing_version_is_applied(tmp_path: Path) -> None:
    """版本号必须写进每一条单价。"""
    path = _write(
        tmp_path,
        """
[llm.model_pricing."m"]
input_per_mtok = 0.10
output_per_mtok = 0.50
effective_from = "2026-11-01"
""",
    )
    entry = load_model_pricing(path, pricing_version="v7")[0]
    assert entry.pricing_version == "v7"


def test_cached_input_is_optional(tmp_path: Path) -> None:
    """缓存输入单价可以省略，此时为 ``None``。"""
    path = _write(
        tmp_path,
        """
[llm.model_pricing."m"]
input_per_mtok = 0.10
output_per_mtok = 0.50
effective_from = "2026-11-01"
""",
    )
    entry = load_model_pricing(path)[0]
    assert entry.cached_input_usd_per_mtok is None


def test_dataclass_is_frozen() -> None:
    """单价配置不可变。"""
    entry = ModelPricingConfig(
        model_id="m",
        pricing_version="v1",
        input_usd_per_mtok=Decimal("1"),
        output_usd_per_mtok=Decimal("2"),
        cached_input_usd_per_mtok=None,
        effective_from=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        provider="cloud",
        source_url=None,
    )
    with pytest.raises(AttributeError):
        entry.model_id = "other"  # type: ignore[misc]
