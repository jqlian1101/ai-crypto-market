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


# --------------------------------------------------------------------------
# 多 Agent 编排（multi_agent.py / agent_persistence.py / agent_registry.py）
# --------------------------------------------------------------------------

#: 多 Agent 模块
MULTI_AGENT_TARGET = "src/aicrypto/services/multi_agent.py"
#: 落库模块
PERSISTENCE_TARGET = "src/aicrypto/services/agent_persistence.py"
#: Agent 注册表
REGISTRY_TARGET = "src/aicrypto/services/agent_registry.py"
#: Master 输出 -> 报告内容的转换层
REPORT_TARGET = "src/aicrypto/services/multi_agent_report.py"
#: 多 Agent 的粘合层（编排 -> 模型 -> 落库）
RUNNER_TARGET = "src/aicrypto/services/multi_agent_runner.py"

#: **调用方传入的档位不再传给 Master**（L-35）。
#:
#: 典型症状：日报/复盘该用的 DEEP 档**永远用不上**，
#: 而报告里写的是「请求档位」——**看起来一切正常**。
_TIER_NOT_PROPAGATED = (
    "if spec.name == MASTER_SPEC.name and requested_tier is not None:",
    "if False:",
)

#: **降级原因不再上报**（L-35）。
#:
#: 降级被静默吞掉：读者以为拿到的是原档位的结论（PRD 24.1「降级必须留痕」）。
_DEGRADATION_DROPPED = (
    "degraded_reason=master_result.degraded_reason if master_result else None,",
    "degraded_reason=None,",
)

#: **计量行不再挂上 ``agent_run_id``**（L-33 的真正修复点）。
#:
#: ``model_runs.agent_run_id`` 回答「这次模型调用属于哪一次 Agent 运行」。
#: 此前 ``record_model_run`` 硬编码 ``agent_run_id=None``，
#: 那个外键列**恒为 NULL**——列建了、索引建了，却从没有写入方。
_AGENT_RUN_ID_NOT_LINKED = (
    "        run_id = run_id_by_agent.get(agent_name) if agent_name else None",
    "        run_id = None",
)

#: **事实/推断/假设的标注被洗掉**（PRD 4.7.2）。
#:
#: 标注是 Master 履行「区分事实、推断和待验证假设」这条纪律的**唯一证据**。
#: 把它洗掉，报告看起来还是「有结论」，
#: 但读者**无法分辨哪句是事实、哪句是猜测**——纪律等于没执行。
#:
#: **注意**：不能简单地把 ``FACT_MARK`` 改成空串——
#: 那样 ``finding[len(FACT_MARK):]`` 会取**整串**（len 为 0），
#: 标注反而**原样留在输出里**，破坏根本不生效。
#: 必须改成「**把标注从输出里删掉**」。
_MARKERS_STRIPPED = (
    'dominant_factors = [f"{FACT_MARK}{item}" for item in facts[:5]] + [\n'
    '        f"{INFERENCE_MARK}{item}" for item in inferences[:5]\n'
    "    ]",
    "dominant_factors = [item for item in facts[:5]] + [\n"
    "        item for item in inferences[:5]\n"
    "    ]",
)

#: **【假设】标注被洗掉**（未验证的推测被抬高成结论）。
_HYPOTHESIS_MARK_STRIPPED = (
    'disagreements.extend(f"{HYPOTHESIS_MARK}{item}" for item in hypotheses[:3])',
    "disagreements.extend(item for item in hypotheses[:3])",
)

#: **未标注的发现被直接丢弃**。
#:
#: 强行分类等于替 Master 做了它没做的判断；
#: 但完全丢弃等于**吞掉信息**——读者会以为 Master 没说过这话。
_UNMARKED_DROPPED = (
    "        else:\n            unclassified.append(finding)",
    "        else:\n            pass",
)

#: **识别不出方向时返回 NEUTRAL 而不是 UNAVAILABLE**。
#:
#: ``NEUTRAL`` 是「我看下来是震荡」——
#: 把它当成「我没结论」，会让「没分析出来」
#: 在报表上看起来像「分析出来是震荡」（PRD 24.4.1 部分结果红线）。
_UNAVAILABLE_BECOMES_NEUTRAL = (
    "    if len(matched) != 1:\n        return BIAS_UNAVAILABLE",
    "    if len(matched) == 0:\n        return BIAS_NEUTRAL",
)

#: **缺失的 Agent 不再显式标注**（PRD 32.4）。
_MISSING_AGENTS_DROPPED = (
    "        uncertainties.append(f\"以下 Agent 未能产出结论，其视角缺失：{'、'.join(missing)}\")",
    "        pass",
)

#: **Master 报告的数据缺口被吞掉**。
#:
#: 算完不落库等于没登记——这正是本项目反复出现的
#: 「**配了 / 算了 / 没人看**」型静默失效。
_MISSING_DATA_DROPPED = (
    '    for item in output.missing_data:\n        uncertainties.append(f"数据缺口：{item}")',
    "    pass",
)

#: **把自然语言证据转成 EvidenceRef**（PRD 4.7.1 禁止虚构引用）。
#:
#: ``EvidenceRef`` 要求精确的 ``source_path`` 与 ``value``——
#: Master 的自然语言给不出这些，硬转会造出**无法定位的引用**。
_EVIDENCE_FABRICATED = (
    "        evidence=[],\n        counter_evidence=[],",
    "        evidence=[],\n"
    "        counter_evidence=[\n"
    "            EvidenceRef(\n"
    '                evidence_id="ev_fabricated",\n'
    '                source_type="model_output",\n'
    '                source_path="summary",\n'
    '                value="fabricated",\n'
    '                snapshot_id="snap_unknown",\n'
    '                interpretation="破坏：凭空造一条无法定位的证据引用",\n'
    "            )\n"
    "        ],",
)

#: **让子 Agent 看到其他分支的结论**——
#: 即 PRD 4.7 协作原则 1 被破坏。
#:
#: 这是本组破坏里**最重要**的一个：
#: 子 Agent 一旦互相可见，6 个 Agent 就退化成 1 个 Agent 调了 6 次，
#: 而且**看起来**做了独立验证、实际没有——这会**虚增**证据的独立性，
#: 进而让 Master 高估置信度。
#:
#: 破坏方式：``Send`` 的分支局部 state 里带上全部快照，
#: 而不是只带本 Agent 的视图——模拟「不小心把 state 传全了」。
_SUB_AGENT_SEES_OTHERS = (
    '                            "snapshot_view": view,',
    '                            "snapshot_view": state["snapshot"],',
)

#: **分歧检测失效**——不再识别 UP vs DOWN。
#:
#: PRD 4.7 协作原则 3：「对重大分歧保留分歧」。
#: 分歧必须**先被识别**才能被保留。
_NO_CONFLICT_DETECTION = (
    "            if not _is_opposite(left_direction, right_direction):\n                continue",
    "            if False:\n                continue",
)

#: **把 FLAT 也算成分歧**——「证据不足」被当成「与看涨矛盾」。
#:
#: 这不是「漏报」而是「误报」：
#: 6 个 Agent 里只要一个方向明确、一个保守，就会产出一堆**假分歧**，
#: 淹没真分歧——而 PRD 要求保留的是「**重大**分歧」。
_FLAT_COUNTS_AS_CONFLICT = (
    '    return {left, right} == {"UP", "DOWN"}',
    '    return {left, right} == {"UP", "DOWN"} or "FLAT" in {left, right}',
)

#: **方向识别过于宽松**——「多空不明」也被判成某个方向。
_DIRECTION_TOO_LOOSE = (
    "    matched = [direction for direction, hit in hits.items() if hit]\n    if len(matched) != 1:",
    "    matched = [direction for direction, hit in hits.items() if hit]\n    if not matched:",
)

#: **子 Agent 异常不再被隔离**——让异常穿出去。
#:
#: PRD 24.4：「超时则输出『分析超时 + 已完成部分』的部分结果，
#: 而不是整体失败」。
#: 若异常穿出去，其他 5 个 Agent 已经算好的结果就**全丢了**。
_NO_EXCEPTION_ISOLATION = (
    '            except Exception as exc:\n                logger.exception("Agent %s 执行失败", spec.name)',
    "            except ZeroDivisionError as exc:\n"
    '                logger.exception("Agent %s 执行失败", spec.name)',
)

#: **失败也写 agent_outputs**——即伪造输出（PRD 32.4 的红线）。
_FAILURE_WRITES_OUTPUT = (
    '    if result.status == "SUCCESS" and result.output is not None:',
    "    if result.output is not None:",
)

#: **agent_runs 不 flush**——``run_id`` 无法被 ``model_runs.agent_run_id`` 引用。
_NO_FLUSH = (
    "    db.flush()\n    return run_id",
    "    return run_id",
)

#: **同源证据被当成多个独立来源**——
#: ``ohlcv`` 域内的 3 个 Agent 被算成 3 个独立来源。
#:
#: PRD 4.7.2：「同源指标或同一行情衍生出的相似观点不能重复计为独立支持。
#: ……独立调用不代表统计独立，Agent 数量不能作为置信度依据。」
_SAME_SOURCE_COUNTED_AS_INDEPENDENT = (
    "    domains: set[str] = set()\n    for spec in specs:\n        domains.update(spec.evidence_domains)\n    return len(domains)",
    "    return len(specs)",
)

#: **证据分组按 Agent 而非按来源域**——
#: Master 收到的分组不再体现「哪些 Agent 同源」。
_EVIDENCE_GROUPS_BY_AGENT = (
    "    groups: dict[str, list[str]] = {}\n"
    "    for spec in specs:\n"
    "        for domain in spec.evidence_domains:\n"
    "            groups.setdefault(domain, []).append(spec.name)\n"
    "    return groups",
    "    return {spec.name: [spec.name] for spec in specs}",
)

MULTI_AGENT_MUTATIONS: list[Mutation] = [
    Mutation(
        "test_sub_agents_cannot_see_each_other",
        "子 Agent 能看到其他分支的结论（伪多 Agent，虚增证据独立性）",
        MULTI_AGENT_TARGET,
        "tests/unit/test_multi_agent_graph.py::test_sub_agents_cannot_see_each_other",
        (_SUB_AGENT_SEES_OTHERS,),
    ),
    Mutation(
        "test_each_sub_agent_gets_its_own_trimmed_view",
        "不再按 Agent 裁剪快照视图（各自独立快照视图失效）",
        MULTI_AGENT_TARGET,
        "tests/unit/test_multi_agent_graph.py::test_each_sub_agent_gets_its_own_trimmed_view",
        (
            (
                "    blocks = {key: value for key, value in snapshot.items() if key in allowed}\n"
                "    return {**metadata, **blocks}",
                "    return snapshot",
            ),
        ),
    ),
    Mutation(
        "test_only_opposite_pairs_are_conflicts",
        "方向性分歧不再被识别（重大分歧会被抹平）",
        MULTI_AGENT_TARGET,
        "tests/unit/test_multi_agent_graph.py::test_only_opposite_pairs_are_conflicts",
        (_NO_CONFLICT_DETECTION,),
    ),
    Mutation(
        "test_flat_is_not_a_conflict_with_directional",
        "「证据不足」被当成与看涨矛盾（假分歧淹没真分歧）",
        MULTI_AGENT_TARGET,
        "tests/unit/test_multi_agent_graph.py::test_flat_is_not_a_conflict_with_directional",
        (_FLAT_COUNTS_AS_CONFLICT,),
    ),
    Mutation(
        "test_detect_direction_returns_none_when_both_directions",
        "方向识别过于宽松（自相矛盾的结论被当成明确方向）",
        MULTI_AGENT_TARGET,
        "tests/unit/test_multi_agent_graph.py::test_detect_direction_returns_none_when_both_directions",
        (_DIRECTION_TOO_LOOSE,),
    ),
    Mutation(
        "test_failure_does_not_abort_the_run",
        "子 Agent 异常不再被隔离（一个失败拖垮整次分析）",
        MULTI_AGENT_TARGET,
        "tests/unit/test_multi_agent_graph.py::test_failure_does_not_abort_the_run",
        (_NO_EXCEPTION_ISOLATION,),
    ),
    Mutation(
        "test_master_failure_is_isolated_too",
        "Master 异常不再被隔离（子 Agent 结果全丢）",
        MULTI_AGENT_TARGET,
        "tests/unit/test_multi_agent_graph.py::test_master_failure_is_isolated_too",
        (
            (
                '        except Exception as exc:\n            logger.exception("Master Agent 执行失败")',
                "        except ZeroDivisionError as exc:\n"
                '            logger.exception("Master Agent 执行失败")',
            ),
        ),
    ),
    Mutation(
        "test_failure_writes_agent_run_but_no_agent_output",
        "失败也写 agent_outputs（伪造输出——PRD 32.4 红线）",
        PERSISTENCE_TARGET,
        "tests/unit/test_agent_persistence.py::test_failure_writes_agent_run_but_no_agent_output",
        (_FAILURE_WRITES_OUTPUT,),
    ),
    Mutation(
        "test_only_success_writes_agent_output",
        "只要有输出对象就写 agent_outputs（状态不再把关）",
        PERSISTENCE_TARGET,
        "tests/unit/test_agent_persistence.py::test_only_success_writes_agent_output[TIMEOUT-False]",
        (_FAILURE_WRITES_OUTPUT,),
    ),
    Mutation(
        "test_start_creates_running_row_and_flushes",
        "agent_runs 不 flush（run_id 无法被 model_runs.agent_run_id 引用）",
        PERSISTENCE_TARGET,
        "tests/unit/test_agent_persistence.py::test_start_creates_running_row_and_flushes",
        (_NO_FLUSH,),
    ),
    Mutation(
        "test_independent_support_count_is_by_domain",
        "同源证据被算成多个独立来源（虚增置信度）",
        REGISTRY_TARGET,
        "tests/unit/test_multi_agent_graph.py::test_independent_support_count_is_by_domain",
        (_SAME_SOURCE_COUNTED_AS_INDEPENDENT,),
    ),
    Mutation(
        "test_evidence_groups_produced_by_graph",
        "证据分组按 Agent 而非按来源域（Master 无法判断同源）",
        REGISTRY_TARGET,
        "tests/unit/test_multi_agent_graph.py::test_evidence_groups_produced_by_graph",
        (_EVIDENCE_GROUPS_BY_AGENT,),
    ),
    # ---- L-35：档位与降级 ----
    Mutation(
        "test_requested_tier_reaches_the_master_call",
        "调用方传入的档位不再传给 Master（DEEP 档永远用不上）",
        RUNNER_TARGET,
        "tests/integration/test_multi_agent_persistence.py::test_requested_tier_reaches_the_master_call",
        (_TIER_NOT_PROPAGATED,),
    ),
    Mutation(
        "test_degradation_is_surfaced_on_the_outcome",
        "降级原因不再上报（读者以为拿到的是原档位的结论）",
        RUNNER_TARGET,
        "tests/integration/test_multi_agent_persistence.py::test_degradation_is_surfaced_on_the_outcome",
        (_DEGRADATION_DROPPED,),
    ),
    Mutation(
        "test_process_one_multi_publishes_report_end_to_end",
        "计量行不再挂上 agent_run_id（「这次调用属于哪次 Agent 运行」答不出来）",
        RUNNER_TARGET,
        "tests/integration/test_multi_agent_worker_e2e.py::test_process_one_multi_publishes_report_end_to_end",
        (_AGENT_RUN_ID_NOT_LINKED,),
    ),
    # ---- 报告转换层：事实/推断/假设的标注 ----
    Mutation(
        "test_fact_marker_is_preserved_in_dominant_factors",
        "【事实】标注被洗掉（事实与猜测无法分辨）",
        REPORT_TARGET,
        "tests/unit/test_multi_agent_report.py::test_fact_marker_is_preserved_in_dominant_factors",
        (_MARKERS_STRIPPED,),
    ),
    Mutation(
        "test_hypothesis_marker_is_preserved_in_disagreements",
        "【假设】标注被洗掉（未验证的推测被抬高成结论）",
        REPORT_TARGET,
        "tests/unit/test_multi_agent_report.py::test_hypothesis_marker_is_preserved_in_disagreements",
        (_HYPOTHESIS_MARK_STRIPPED,),
    ),
    Mutation(
        "test_unmarked_findings_are_preserved_not_dropped",
        "未标注的发现被直接丢弃（吞掉 Master 说过的信息）",
        REPORT_TARGET,
        "tests/unit/test_multi_agent_report.py::test_unmarked_findings_are_preserved_not_dropped",
        (_UNMARKED_DROPPED,),
    ),
    Mutation(
        "test_unclear_summary_maps_to_unavailable_not_neutral",
        "识别不出方向时返回 NEUTRAL（「没结论」被伪装成「震荡」）",
        REPORT_TARGET,
        "tests/unit/test_multi_agent_report.py::test_unclear_summary_maps_to_unavailable_not_neutral",
        (_UNAVAILABLE_BECOMES_NEUTRAL,),
    ),
    Mutation(
        "test_missing_agents_are_explicitly_annotated",
        "缺失的 Agent 不再显式标注（PRD 32.4 的「如实登记缺失」失效）",
        REPORT_TARGET,
        "tests/unit/test_multi_agent_report.py::test_missing_agents_are_explicitly_annotated",
        (_MISSING_AGENTS_DROPPED,),
    ),
    Mutation(
        "test_master_reported_missing_data_is_not_dropped",
        "Master 报告的数据缺口被吞掉（算完不落库等于没登记）",
        REPORT_TARGET,
        "tests/unit/test_multi_agent_report.py::test_master_reported_missing_data_is_not_dropped",
        (_MISSING_DATA_DROPPED,),
    ),
    Mutation(
        "test_no_evidence_refs_are_fabricated",
        "把自然语言证据转成 EvidenceRef（虚构引用）",
        REPORT_TARGET,
        "tests/unit/test_multi_agent_report.py::test_no_evidence_refs_are_fabricated",
        (_EVIDENCE_FABRICATED,),
    ),
]


MUTATIONS: list[Mutation] = AUDIT_MUTATIONS + COST_MUTATIONS + MULTI_AGENT_MUTATIONS


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
