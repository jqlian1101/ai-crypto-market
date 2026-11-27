"""反向验证工具：逐个「破坏」实现，确认对应用例会失败。

**为什么需要这个工具**：一个永远绿的测试可能什么也没验证。
本项目已多次出现「测试通过但缺陷仍在」的情况
（见 ``docs/遗留问题.md`` 的 L-07a / L-25 / L-30：单元测试全绿、真跑才暴露），
因此这里把「反向验证」固化成可重复执行的工具。

**做法**：对实现打一个**语义等价于目标缺陷**的补丁，
确认对应用例**确实失败**，然后恢复实现。
每个用例都必须能锁住它声称要锁的缺陷。

**一次破坏可能需要多处同时改**（例如「让 settings.py 也参与统计」
需要同时改文件筛选与消费判定），因此用**编辑列表**而不是单对。

**锚点失配会显式 SKIP 而不是静默跳过**——
静默跳过等于「没验证过却显示通过」，那正是本工具要消灭的模式。

**多目标文件**：不同模块的实现住在不同文件里，
所以每组破坏声明自己的**目标文件**与**测试文件**。

用法::

    python scripts/verify_test_effectiveness.py

退出码 0 = 全部用例都能锁住对应缺陷；1 = 有用例锁不住。
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 依赖对账模块
AUDIT_TARGET = "src/aicrypto/config/dependency_audit.py"
#: 成本报表模块
COST_TARGET = "src/aicrypto/services/cost_report.py"


@dataclass(frozen=True, slots=True)
class Mutation:
    """一组破坏。

    ``target`` 是被改动的实现文件（相对仓库根），
    ``test`` 是对应用例的 pytest 节点 ID。
    ``edits`` 是 ``(旧代码, 新代码)`` 列表——一次破坏可能需要多处同时改。
    """

    name: str
    desc: str
    target: str
    test: str
    edits: tuple[tuple[str, str], ...]


# --------------------------------------------------------------------------
# 依赖对账（dependency_audit.py）
# --------------------------------------------------------------------------

#: 让「settings.py 也参与消费方统计」的两处改动
#:
#: 注意：``ruff format`` 会把这段列表推导压成一行——
#: 锚点必须与格式化后的实际形态一致，否则会静默 SKIP。
_SETTINGS_INCLUDED = (
    "    runtime_files = [\n"
    "        path for path in _iter_python_files(project_root, *SOURCE_DIRS) if path != settings_path\n"
    "    ]",
    "    runtime_files = list(_iter_python_files(project_root, *SOURCE_DIRS))",
)

#: 让 ``self.X`` 被当成 Settings 字段读取
_SELF_IS_SETTINGS = (
    "    names: set[str] = set()\n    for node in ast.walk(tree):",
    '    names: set[str] = {"self"}\n    for node in ast.walk(tree):',
)

#: 让「任意 ``X.大写属性``」都算消费方（过宽实现）
_ANY_BASE_IS_SETTINGS = (
    "                if base.id not in typed_names:\n                    continue",
    "                if False:\n                    continue",
)

AUDIT_MUTATIONS: list[Mutation] = [
    Mutation(
        "test_alias_variable_counts_as_consumer",
        "别名变量追踪失效（cfg = get_settings() 不再被识别）",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_alias_variable_counts_as_consumer",
        (
            (
                '            if name not in ("get_settings", "resolve_settings"):\n                continue',
                "            if True:\n                continue",
            ),
        ),
    ),
    Mutation(
        "test_instance_attribute_counts_as_consumer",
        "实例属性追踪失效（self._settings.X 不再被识别）",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_instance_attribute_counts_as_consumer",
        (
            (
                "elif (\n                isinstance(base, ast.Attribute)",
                "elif False and (\n                isinstance(base, ast.Attribute)",
            ),
        ),
    ),
    Mutation(
        "test_property_accessor_counts_as_consumer",
        "property 追踪失效（cfg.allowed_ips 不再算消费方）",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_property_accessor_counts_as_consumer",
        (("elif node.attr in property_map:", "elif False:"),),
    ),
    Mutation(
        "test_check_catches_unconsumed_dependency",
        "零消费依赖不再被拦",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_check_catches_unconsumed_dependency",
        (("if usage.distribution in allowed:", "if True:"),),
    ),
    Mutation(
        "test_check_ignores_test_only_usage",
        "「仅测试在用」也算已消费",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_check_ignores_test_only_usage",
        (
            (
                "return bool(self.runtime_imports) or bool(self.other_evidence)",
                "return bool(self.runtime_imports) or bool(self.other_evidence) or bool(self.test_imports)",
            ),
        ),
    ),
    Mutation(
        "test_ast_import_detection_ignores_comments_and_strings",
        "退回「文本也算 import」的行为",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_ast_import_detection_ignores_comments_and_strings",
        (
            (
                '        modules.add(alias.name.split(".")[0])',
                '        modules.add(alias.name.split(".")[0])\n'
                "        import re as _re\n"
                '        for _m in _re.finditer(r"import\\s+([A-Za-z_][A-Za-z0-9_]*)", '
                "path.read_text(encoding='utf-8')):\n"
                "            modules.add(_m.group(1))",
            ),
        ),
    ),
    Mutation(
        "test_ast_import_detection_handles_relative_imports",
        "相对导入被当成第三方依赖",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_ast_import_detection_handles_relative_imports",
        (
            (
                "            if node.level and node.level > 0:\n"
                "                # 相对导入（from . import x）：模块名是项目内的，不是第三方依赖\n"
                "                continue",
                "            if False:\n                pass",
            ),
        ),
    ),
    Mutation(
        "test_whitelist_entry_without_reason_is_rejected",
        "允许无原因的白名单条目",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_whitelist_entry_without_reason_is_rejected",
        (("if not entry.reason.strip():", "if False:"),),
    ),
    Mutation(
        "test_whitelist_has_no_stale_entries",
        "不再检查依赖侧「白名单已过期」",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_whitelist_has_no_stale_entries",
        (("if entry.key in consumed_dists:", "if False:"),),
    ),
    Mutation(
        "test_stale_whitelist_entry_is_reported",
        "不再报告「白名单已过期」（合成样本）",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_stale_whitelist_entry_is_reported",
        (("if entry.key in consumed_dists:", "if False:"),),
    ),
    Mutation(
        "test_no_dependency_is_only_used_by_tests",
        "不再报告「仅测试在用」的依赖（仓库级）",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_no_dependency_is_only_used_by_tests",
        (
            (
                "        if usage.test_imports:\n"
                "            detail = f\"（仅测试在用：{', '.join(usage.test_imports[:3])}）\"",
                "        if False:\n"
                "            detail = f\"（仅测试在用：{', '.join(usage.test_imports[:3])}）\"",
            ),
        ),
    ),
    Mutation(
        "test_test_only_dependency_is_reported",
        "「仅测试在用」不再被标记为 test_only（属性层）",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_test_only_dependency_is_reported",
        (
            (
                "        return not self.is_consumed and bool(self.test_imports)",
                "        return False",
            ),
        ),
    ),
    Mutation(
        "test_toolchain_distributions_are_excluded",
        "不再排除工具链（仓库级）",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_toolchain_distributions_are_excluded",
        (("if dist in TOOLCHAIN_DISTRIBUTIONS or dist in seen:", "if dist in seen:"),),
    ),
    Mutation(
        "test_toolchain_excluded_from_audit",
        "不再排除工具链（合成样本）",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_toolchain_excluded_from_audit",
        (("if dist in TOOLCHAIN_DISTRIBUTIONS or dist in seen:", "if dist in seen:"),),
    ),
    Mutation(
        "test_self_validation_is_not_a_consumer",
        "settings.py 内部的自校验被算作消费方",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_self_validation_is_not_a_consumer",
        (_SETTINGS_INCLUDED, _SELF_IS_SETTINGS),
    ),
    Mutation(
        "test_self_validation_inside_settings_is_not_a_consumer",
        "同上（仓库级）：settings.py 也参与消费方统计",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_self_validation_inside_settings_is_not_a_consumer",
        (_SETTINGS_INCLUDED, _SELF_IS_SETTINGS),
    ),
    Mutation(
        "test_real_consumer_counts_as_consumer",
        "真实消费方不再被识别",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_real_consumer_counts_as_consumer",
        (
            (
                "if node.attr.isupper():",
                "if node.attr.isupper() and node.attr != 'REALLY_USED':",
            ),
        ),
    ),
    Mutation(
        "test_unrelated_class_member_is_not_a_consumer",
        "「任意类的大写属性」都算消费方（过宽实现）",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_unrelated_class_member_is_not_a_consumer",
        (_ANY_BASE_IS_SETTINGS,),
    ),
    Mutation(
        "test_unrelated_enum_member_is_not_a_consumer",
        "同上：过宽实现把非 Settings 类的同名成员误算成消费方",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_unrelated_enum_member_is_not_a_consumer",
        (_ANY_BASE_IS_SETTINGS,),
    ),
    Mutation(
        "test_all_settings_fields_have_a_consumer",
        "同上：过宽实现会掩盖真实缺口",
        AUDIT_TARGET,
        "tests/unit/test_dependency_usage.py::test_all_settings_fields_have_a_consumer",
        (_ANY_BASE_IS_SETTINGS,),
    ),
]


# --------------------------------------------------------------------------
# 成本报表（cost_report.py）
# --------------------------------------------------------------------------

#: 让「未定价」也被当成「已定价」——即把 0 费用直接累加。
#: 这正是本模块要消灭的缺陷：**未定价 ≠ 免费**。
_UNPRICED_COUNTS_AS_PRICED = (
    '        if row.priced:\n            slot["cost_usd"] += row.cost_usd',
    '        if True:\n            slot["cost_usd"] += row.cost_usd',
)

#: 让「已定价」也被当成「未定价」——即把真实费用全部丢掉。
_PRICED_COUNTS_AS_UNPRICED = (
    '        if row.priced:\n            slot["cost_usd"] += row.cost_usd',
    '        if False:\n            slot["cost_usd"] += row.cost_usd',
)

#: 快照里缺 ``priced`` 标记时按「已定价」处理（与保守方向相反）
_MISSING_PRICED_IS_PRICED = (
    '    if isinstance(value, str):\n        return value.strip().lower() == "true"\n    return False',
    '    if isinstance(value, str):\n        return value.strip().lower() == "true"\n    return True',
)

COST_MUTATIONS: list[Mutation] = [
    Mutation(
        "test_unpriced_row_does_not_contribute_cost",
        "未定价行的费用被计入总额（未定价 ≠ 免费被破坏）",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_unpriced_row_does_not_contribute_cost",
        (_UNPRICED_COUNTS_AS_PRICED,),
    ),
    Mutation(
        "test_report_flags_unpriced_as_lower_bound",
        "报表不再声明「费用是下界」",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_report_flags_unpriced_as_lower_bound",
        (
            (
                "    @property\n"
                "    def has_unpriced(self) -> bool:\n"
                '        """是否存在未定价的调用。\n'
                "\n"
                "        为 ``True`` 时，``total.cost_usd`` 是**下界**而非实际支出。\n"
                '        """\n'
                "        return self.total.unpriced_calls > 0",
                "    @property\n"
                "    def has_unpriced(self) -> bool:\n"
                '        """是否存在未定价的调用。"""\n'
                "        return False",
            ),
        ),
    ),
    Mutation(
        "test_unpriced_row_tokens_still_counted",
        "未定价行的 token 不再计入总量（资源消耗被隐藏）",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_unpriced_row_tokens_still_counted",
        (
            (
                '        slot["calls"] += 1\n'
                '        slot["input_tokens"] += row.input_tokens\n'
                '        slot["output_tokens"] += row.output_tokens',
                '        slot["calls"] += 1\n'
                "        if row.priced:\n"
                '            slot["input_tokens"] += row.input_tokens\n'
                '            slot["output_tokens"] += row.output_tokens',
            ),
        ),
    ),
    Mutation(
        "test_unpriced_models_are_listed_separately",
        "未定价模型不再单独列出（运维无法据此补单价）",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_unpriced_models_are_listed_separately",
        (
            (
                "    unpriced_slots: dict[str, dict[str, int]] = {}\n"
                "    for row in materialized:\n"
                "        if row.priced:\n"
                "            continue",
                "    unpriced_slots: dict[str, dict[str, int]] = {}\n"
                "    for row in materialized:\n"
                "        if False:\n"
                "            continue",
            ),
        ),
    ),
    Mutation(
        "test_snapshot_missing_priced_key_is_unpriced",
        "快照缺 priced 标记时按「已定价」处理（把「没标记」误读成「免费」）",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_snapshot_missing_priced_key_is_unpriced",
        (_MISSING_PRICED_IS_PRICED,),
    ),
    Mutation(
        "test_group_by_prompt_version",
        "不再按 Prompt 版本分组（「哪版最烧 token」无法回答）",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_group_by_prompt_version",
        (
            (
                "        by_prompt_version=_sort_by_cost(\n"
                "            _aggregate(materialized, lambda row: row.prompt_version or UNKNOWN)\n"
                "        ),",
                "        by_prompt_version=[],",
            ),
        ),
    ),
    Mutation(
        "test_group_by_tier",
        "不再按档位分组",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_group_by_tier",
        (
            (
                "        by_tier=_sort_by_cost(_aggregate(materialized, lambda row: row.tier or UNKNOWN)),",
                "        by_tier=[],",
            ),
        ),
    ),
    Mutation(
        "test_group_by_agent",
        "不再按 Agent 分组",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_group_by_agent",
        (
            (
                "        by_agent=_sort_by_cost(_aggregate(materialized, lambda row: row.agent_name or UNKNOWN)),",
                "        by_agent=[],",
            ),
        ),
    ),
    Mutation(
        "test_group_by_status",
        "不再按状态分组（失败成本不可见）",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_group_by_status",
        (
            (
                "        by_status=_sort_by_cost(_aggregate(materialized, lambda row: row.status or UNKNOWN)),",
                "        by_status=[],",
            ),
        ),
    ),
    Mutation(
        "test_group_by_day",
        "不再按日分组",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_group_by_day",
        (
            (
                "        by_day=sorted(\n"
                "            _aggregate(materialized, lambda row: row.created_at.date().isoformat()),\n"
                "            key=lambda b: b.key,\n"
                "        ),",
                "        by_day=[],",
            ),
        ),
    ),
    Mutation(
        "test_null_dimension_becomes_unknown",
        "维度为 NULL 时不再归入「(未知)」（行会悄悄消失）",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_null_dimension_becomes_unknown",
        (
            (
                "        by_tier=_sort_by_cost(_aggregate(materialized, lambda row: row.tier or UNKNOWN)),",
                '        by_tier=_sort_by_cost(_aggregate(materialized, lambda row: row.tier or "")),',
            ),
        ),
    ),
    Mutation(
        "test_buckets_sorted_by_cost_desc",
        "分组不再按费用降序（最贵的排不最前）",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_buckets_sorted_by_cost_desc",
        (
            (
                "    return sorted(buckets, key=lambda b: (b.cost_usd, b.total_tokens), reverse=True)",
                "    return sorted(buckets, key=lambda b: b.key)",
            ),
        ),
    ),
    Mutation(
        "test_render_warns_on_unpriced",
        "报表不再警告未定价调用",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_render_warns_on_unpriced",
        (
            (
                '    if report.has_unpriced:\n        lines.append("!! 警告：存在未定价调用")',
                '    if False:\n        lines.append("!! 警告：存在未定价调用")',
            ),
        ),
    ),
    Mutation(
        "test_render_lists_unpriced_models",
        "报表不再列出未定价模型名",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_render_lists_unpriced_models",
        (
            (
                '        if report.unpriced_models:\n            models = "、".join(',
                '        if False:\n            models = "、".join(',
            ),
        ),
    ),
    Mutation(
        "test_priced_rows_do_not_contribute_when_misflagged",
        "已定价行被误当未定价（真实费用被丢掉）",
        COST_TARGET,
        "tests/unit/test_cost_report.py::test_unpriced_row_does_not_contribute_cost",
        (_PRICED_COUNTS_AS_UNPRICED,),
    ),
]


MUTATIONS: list[Mutation] = AUDIT_MUTATIONS + COST_MUTATIONS


def run_tests(node_id: str) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", node_id, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0, proc.stdout[-1500:]


def main() -> int:
    failures: list[str] = []
    originals: dict[str, str] = {}
    try:
        for mutation in MUTATIONS:
            path = ROOT / mutation.target
            if mutation.target not in originals:
                originals[mutation.target] = path.read_text(encoding="utf-8")
            original = originals[mutation.target]

            mutated = original
            missing = False
            for old, new in mutation.edits:
                if old not in mutated:
                    missing = True
                    break
                mutated = mutated.replace(old, new, 1)
            if missing:
                failures.append(f"[{mutation.name}] 破坏锚点未找到，无法验证：{mutation.desc}")
                print(f"SKIP {mutation.name}: 锚点未找到")
                continue
            path.write_text(mutated, encoding="utf-8")
            ok, _out = run_tests(mutation.test)
            path.write_text(original, encoding="utf-8")
            if ok:
                failures.append(
                    f"[{mutation.name}] 破坏后测试**仍然通过**——该用例锁不住这个缺陷：{mutation.desc}"
                )
                print(f"FAIL {mutation.name}: 破坏后仍通过 -> {mutation.desc}")
            else:
                print(f"OK   {mutation.name}: 破坏后正确失败 -> {mutation.desc}")
    finally:
        for target, content in originals.items():
            (ROOT / target).write_text(content, encoding="utf-8")

    print()
    if failures:
        print("=== 验证失败 ===")
        for f in failures:
            print(" ", f)
        return 1
    print(f"=== 全部反向验证通过：{len(MUTATIONS)} 个用例都能锁住对应缺陷 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
