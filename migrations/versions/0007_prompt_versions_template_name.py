"""V0007: ``prompt_versions`` 主键补上模板名（PRD 24.5）。

**缺陷**：``prompt_versions`` 的主键是 ``(agent_name, prompt_version)``，
但同一个 Agent 下**每个 Prompt 模板各有独立版本号**——
``prompts/master/analysis.v1.md``（system）与
``prompts/master/analysis.user.v1.md``（user）**都是 ``(master, v1)``**。

结果是 user 模板的登记被**静默丢弃**：``ON CONFLICT DO NOTHING``
按 ``(agent_name, prompt_version)`` 冲突，两条记录撞在一起，
只剩 system 那条。

**后果**：Prompt 版本审计**不完整**——user 模板的哈希从来没进过库，
而 user 模板承载了「快照怎么渲染给模型」的全部指令。
「改一个字的 Prompt 无法追溯是哪一版产出」（PRD 24.5 的核心诉求）
对 user 模板完全失效。

**修复**：主键改为 ``(agent_name, template_name, prompt_version)``。
这是 PRD 24.5 语义的正名——``prompt_version`` 本来就是
「某个模板的版本」，而非「某个 Agent 的版本」。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# 注意：revision id 最长 32 字符（``alembic_version.version_num`` 是 varchar(32)）。
revision: str = "0007_prompt_versions_tmpl_name"
down_revision: str | None = "0006_model_pricing"
branch_labels: str | None = None
depends_on: str | None = None

_OLD_PK = "pk_prompt_versions"
_NEW_PK = "pk_prompt_versions"

#: 从 ``template_path`` 反推 ``template_name`` 的 SQL **表达式**（不含 SELECT）。
#:
#: **提取成模块级常量，是为了让回归测试能直接引用它**
#: （见 ``tests/integration/test_prompt_versions_backfill.py``）。
#: 此前测试里抄了一份 SQL 副本——改迁移时忘改测试，
#: 就会留下「测试全绿但迁移已经错了」的假象。
#: **一份实现只能有一个来源**。
#:
#: 规则：先剥扩展名（``.md`` / ``.j2``），再剥 ``.v<n>`` 版本号。
#: 不能用 ``split_part(..., '.', 1)`` 取第一段——
#: 那样 ``analysis.user.v1.md`` 会得到 ``analysis``（丢掉 ``.user``）。
BACKFILL_TEMPLATE_NAME_EXPR = """
            regexp_replace(
                regexp_replace(
                    split_part({path_expr}, '/', 3),
                    '\\.(md|j2)$', ''
                ),
                '\\.v[0-9]+$', ''
            )
"""

#: 同上，但用于**测试**的 SELECT 形式。
BACKFILL_TEMPLATE_NAME_SQL = "SELECT " + BACKFILL_TEMPLATE_NAME_EXPR.format(path_expr=":path")


def upgrade() -> None:
    # 1. 加列（先加后迁移，避免 NOT NULL 约束在已有数据上失败）
    op.add_column(
        "prompt_versions",
        sa.Column("template_name", sa.Text(), nullable=True),
    )

    # 2. 回填：已有行的模板名从 template_path 的文件名反推。
    #    路径形如 ``prompts/master/analysis.v1.md`` -> ``analysis``；
    #    ``prompts/master/analysis.user.v1.md`` -> ``analysis.user``。
    #
    #    **必须先剥掉扩展名，再剥 .v<n> 版本号**：
    #    直接用 ``split_part(..., '.', 1)`` 会在带点的模板名上截断——
    #    ``analysis.user.v1.md`` 会得到 ``analysis``（丢掉 ``.user``）。
    #
    #    **扩展名必须同时处理 ``.md`` 与 ``.j2``**：PRD 24.5 明确允许
    #    ``prompts/<agent>/<template>.v{n}.j2``（或 ``.md``）两种形态。
    #    只剥 ``.md`` 的话，``.j2`` 文件会回填出 ``analysis.v1.j2``
    #    这种**带版本号的模板名**——主键语义直接错了：
    #    同一个模板的 ``v1`` 与 ``v2`` 会被当成**两个不同的模板**，
    #    恰好制造出本次修复要消灭的那类「该在的东西不在」问题。
    op.execute(
        sa.text(
            "UPDATE prompt_versions SET template_name = "
            + BACKFILL_TEMPLATE_NAME_EXPR.format(path_expr="template_path")
            + " WHERE template_name IS NULL"
        )
    )
    # 回填后仍为空的（路径异常）给一个占位值，避免 NULL 破坏 NOT NULL
    op.execute(
        sa.text(
            """
            UPDATE prompt_versions
            SET template_name = 'unknown'
            WHERE template_name IS NULL OR template_name = ''
            """
        )
    )

    # 3. 改成 NOT NULL
    op.alter_column(
        "prompt_versions",
        "template_name",
        existing_type=sa.Text(),
        nullable=False,
    )

    # 4. 换主键：先删旧约束，再建新约束
    op.drop_constraint(_OLD_PK, "prompt_versions", type_="primary")
    op.create_primary_key(
        _NEW_PK,
        "prompt_versions",
        ["agent_name", "template_name", "prompt_version"],
    )


def downgrade() -> None:
    # 回滚主键前，先处理可能因新主键而多出来的行——
    # 同一 (agent_name, prompt_version) 可能对应多个 template_name。
    # 保留 template_path 排序后的第一条，其余删除，
    # 否则重建旧主键会撞唯一约束。
    op.execute(
        sa.text(
            """
            DELETE FROM prompt_versions a
            USING prompt_versions b
            WHERE a.agent_name = b.agent_name
              AND a.prompt_version = b.prompt_version
              AND a.template_name > b.template_name
            """
        )
    )
    op.drop_constraint(_NEW_PK, "prompt_versions", type_="primary")
    op.create_primary_key(
        _OLD_PK,
        "prompt_versions",
        ["agent_name", "prompt_version"],
    )
    op.drop_column("prompt_versions", "template_name")
