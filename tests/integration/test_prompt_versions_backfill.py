"""0007 迁移的**回填表达式**回归测试（真库）。

**背景**：0007 把 ``prompt_versions`` 主键从
``(agent_name, prompt_version)`` 改成三元组，
其中 ``template_name`` 由 ``template_path`` **回填**。

回填分支只在**表里已有数据**时才走到——
全新库迁移时表是空的，一行都不会执行。
所以「全新库迁移正常」**不代表**迁移正确**。

**曾经真实踩过的两个坑**：
1. 用 ``split_part(path, '.', 1)`` 取模板名 ->
   ``analysis.user.v1.md`` 得到 ``analysis``（丢掉 ``.user``）；
2. 只剥 ``.md`` 后缀 -> ``.j2`` 文件回填出 ``analysis.v1.j2``，
   **带版本号的模板名**，主键语义直接错了：
   同一模板的 ``v1`` 与 ``v2`` 会被当成**两个不同的模板**，
   恰好制造出本次修复要消灭的那类「该在的东西不在」问题。

**为什么不用真实迁移来测**：真实迁移要 downgrade/upgrade，
会**改动共享测试库的表结构**——CI 里并行跑测试时这会污染其他用例。
本测试改为**直接验证迁移里那条 UPDATE 的表达式语义**：
同样的 SQL、同样的数据库、同样的正则引擎，
但**只读**，不碰 schema。
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

DB_URL = "postgresql+psycopg://aicrypto:aicrypto@127.0.0.1:15432/aicrypto"


#: 直接引用**迁移模块里的常量**，不在测试里抄一份 SQL。
#:
#: 此前测试抄了一份 SQL 副本——改迁移时忘改测试，
#: 就会留下「测试全绿但迁移已经错了」的假象。
#: **一份实现只能有一个来源**：测试引用实现，
#: 实现改了测试自动跟着变，回退实现测试立刻失败。
def _load_migration() -> object:
    """按路径加载 0007 迁移模块。

    模块名以数字开头（``0007_...``），**不能**用常规 import 语句，
    只能用 ``importlib`` 按文件路径加载。
    """
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0007_prompt_versions_template_name.py"
    )
    spec = importlib.util.spec_from_file_location("m0007", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BACKFILL_SQL = _load_migration().BACKFILL_TEMPLATE_NAME_SQL  # type: ignore[attr-defined]

#: (template_path, 期望的 template_name)
CASES = [
    ("prompts/master/analysis.v1.md", "analysis"),
    ("prompts/master/analysis.user.v1.md", "analysis.user"),
    ("prompts/master/analysis.v10.md", "analysis"),
    ("prompts/master/analysis.user.v2.md", "analysis.user"),
    # **PRD 24.5 明确允许 .j2**——曾经漏剥这个后缀，
    # 回填出带版本号的模板名。
    ("prompts/master/analysis.v1.j2", "analysis"),
    ("prompts/master/analysis.user.v3.j2", "analysis.user"),
]


@pytest.fixture
def db_env():
    url = os.getenv("ANALYSIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 ANALYSIS_TEST_DATABASE_URL 以执行 PostgreSQL 集成测试")
    engine = create_engine(url)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        engine.dispose()


def test_backfill_expression_extracts_template_name(db_env: Session) -> None:
    """回填表达式必须从各种模板路径形态里正确取出模板名。

    **核心断言**：模板名里**不得残留版本号**——
    残留了就会让同一个模板的 v1 / v2 变成两个模板。
    """
    for path, expected in CASES:
        actual = db_env.execute(text(_BACKFILL_SQL), {"path": path}).scalar_one()
        assert actual == expected, f"{path} 回填为 {actual!r}，期望 {expected!r}"
        assert ".v" not in actual, (
            f"{path} 回填出 {actual!r}：**残留版本号**，同一模板的 v1/v2 会被当成两个不同的模板"
        )


def test_backfill_expression_matches_prompt_loader(db_env: Session) -> None:
    """回填的模板名必须与**运行时加载器**解析出的模板名一致。

    这是最关键的一致性断言：数据库里的 ``template_name``
    与 ``config/prompts.py`` 算出的 ``template_name`` 必须**逐字相同**，
    否则「运行时认为这是模板 A、数据库认为这是模板 B」——
    ``ON CONFLICT`` 就会失配，登记静默丢弃。
    """
    from aicrypto.config.prompts import parse_template_filename

    for path, expected in CASES:
        filename = path.rsplit("/", 1)[-1]
        if not filename.endswith(".md"):
            # 运行时目前只支持 .md（``list_templates`` 过滤 ``.md``），
            # 所以只对 .md 断言两边一致。
            continue
        runtime_name, _version = parse_template_filename(filename)
        assert runtime_name == expected, (
            f"{path}：运行时解析为 {runtime_name!r}，回填表达式得到 {expected!r}——**两边不一致**"
        )


def test_prompt_versions_primary_key_is_triple(db_env: Session) -> None:
    """主键必须是三元组 ``(agent_name, template_name, prompt_version)``。

    **必须读 ``pg_constraint.constraintdef``**：
    Postgres 的索引列按**表的物理列序**存储，
    与 ``create_primary_key`` 传入的顺序无关——
    按索引列判断会误判成「主键不对」。
    """
    definition = db_env.execute(
        text(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid = 'prompt_versions'::regclass and contype = 'p'"
        )
    ).scalar_one()
    assert definition == "PRIMARY KEY (agent_name, template_name, prompt_version)", (
        f"prompt_versions 主键不是三元组，实际 {definition}"
    )
