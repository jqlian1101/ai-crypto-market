"""数据库基座（PRD 第 30.1 / 30.2 章）。

核心约定（30.1）：

===========================  ==========================================
约定                          规则
===========================  ==========================================
时间                          一律 ``TIMESTAMPTZ``，存 UTC
主键                          一律 ``TEXT``，带前缀的可读 ID
价格 / 数量                   ``NUMERIC(38, 12)``
资金 / 权益                   ``NUMERIC(38, 8)``
比率 / 置信度                 ``NUMERIC(10, 6)``（0–1，百分比存小数）
结构化输出                     ``JSONB``
布尔                          ``BOOLEAN``，非空且有默认值
枚举                          ``TEXT`` + ``CHECK`` 约束（不用 PG ENUM）
命名                          表名复数、小写下划线；``idx_<table>_<cols>`` /
                             ``uq_<table>_<cols>`` / ``ck_<table>_<rule>``
===========================  ==========================================
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """全局 declarative base。

    所有 ORM 模型继承它，命名约定在此统一（36.7）。
    """

    type_annotation_map = {
        datetime: DateTime(timezone=True),
        bool: Boolean,
        int: Integer,
        str: String,
    }

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        pk = getattr(self, "id", None) or getattr(self, "__dict__", {})
        return f"<{type(self).__name__} {pk}>"


# --------------------------------------------------------------------------
# 列类型别名 —— 与 30.1 的约定一一对应，业务模型直接引用这些名字，
# 避免各处手写 Numeric(38, 12) 造成漂移。
# --------------------------------------------------------------------------
PriceType = Numeric(38, 12)
QuantityType = Numeric(38, 12)
MoneyType = Numeric(38, 8)
RatioType = Numeric(10, 6)
RateType = Numeric(18, 10)
BpsType = Numeric(12, 4)
TimestampType = DateTime(timezone=True)
JsonType = JSONB
IdType = Text


def pk_column() -> Mapped[str]:
    """主键列：``TEXT`` 类型。"""
    return mapped_column(Text, primary_key=True)


def ts_column(*, nullable: bool = False, index: bool = False) -> Mapped[datetime]:
    """时间戳列：一律 ``TIMESTAMPTZ``。"""
    return mapped_column(TimestampType, nullable=nullable, index=index)


def fk_column(target: str, *, nullable: bool = False) -> Mapped[str]:
    """外键列：``TEXT`` 类型。"""
    return mapped_column(Text, ForeignKey(target), nullable=nullable)


def bool_column(*, default: bool = False) -> Mapped[bool]:
    """布尔列：非空且有默认值（30.1）。"""
    return mapped_column(Boolean, nullable=False, default=default)


def json_column(*, nullable: bool = False) -> Mapped[Any]:
    """JSONB 列。"""
    return mapped_column(JsonType, nullable=nullable)


def idx(*columns: str) -> Index:
    """索引工厂：``idx_<table>_<cols>``（30.1 命名约定）。"""
    return Index(*columns)


def uq(*columns: str, name: str) -> Any:
    """唯一约束工厂：``uq_<table>_<cols>``。"""
    return UniqueConstraint(*columns, name=name)


def ck(name: str, condition: str) -> CheckConstraint:
    """CHECK 约束工厂：``ck_<table>_<rule>``。

    枚举用 ``TEXT`` + ``CHECK``（30.1：避免 PG ENUM 后续加值需改类型）。
    """
    return CheckConstraint(condition, name=name)


def check_in(column: str, values: tuple[str, ...], *, table: str, rule: str) -> CheckConstraint:
    """生成 ``column IN (...)`` 形式的 CHECK 约束。"""
    rendered = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(
        f"{column} IN ({rendered})",
        name=f"ck_{table}_{rule}",
    )


__all__ = [
    "Base",
    "BpsType",
    "CheckConstraint",
    "ForeignKey",
    "Index",
    "Integer",
    "JsonType",
    "MoneyType",
    "Numeric",
    "PriceType",
    "QuantityType",
    "RateType",
    "RatioType",
    "SmallInteger",
    "String",
    "Text",
    "bool_column",
    "check_in",
    "ck",
    "fk_column",
    "idx",
    "json_column",
    "pk_column",
    "ts_column",
    "uq",
]
