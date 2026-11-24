"""回归测试：入库幂等判定不得依赖 ``result.rowcount``（PRD 29.7）。

**背景**：SQLAlchemy 的 ORM ``insert`` 会自动追加 ``RETURNING`` 以取回主键，
此时 psycopg 的 ``cursor.rowcount`` 返回 ``-1``（未知）而非 0/1。
因此 ``if result.rowcount:`` 的真假判断在 ``ON CONFLICT DO NOTHING`` 下
**永远成立**，会把"冲突跳过"误判成"已插入"。

这类缺陷在纯 mock 单测里不可见，必须在真实 PostgreSQL 上验证：
数据库层面行数确实只有一条（``DO NOTHING`` 生效），但**返回的判定结果**错了。

本模块用 AST 静态检查锁定该约定，防止再次无声引入。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "aicrypto"


def _python_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _uses_rowcount(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "rowcount":
            return True
    return False


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_rowcount_dependency_in_src(path: Path) -> None:
    """源码中不得出现 ``.rowcount`` 依赖。

    ``ON CONFLICT DO NOTHING`` 下必须用 ``RETURNING`` 的返回行数判定，
    而不是 ``rowcount``。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert not _uses_rowcount(tree), (
        f"{path.name} 使用了 result.rowcount 判定写入结果；"
        "在 ON CONFLICT DO NOTHING 下 rowcount 为 -1（未知），判定会永远成立。"
        "请改用 .returning(<pk>).scalar_one_or_none()。"
    )


def test_derivatives_ingest_uses_returning_for_all_dedup_writes() -> None:
    """``derivatives_ingest`` 的每处去重写入都必须带 ``RETURNING``。"""
    path = SRC_ROOT / "services" / "derivatives_ingest.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("persist_")
    }
    assert set(functions) >= {
        "persist_trade",
        "persist_orderbook",
        "persist_open_interest",
        "persist_funding_rate",
    }

    for name, func in functions.items():
        has_returning = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "returning"
            for node in ast.walk(func)
        )
        assert has_returning, f"{name} 缺少 RETURNING 判定"
