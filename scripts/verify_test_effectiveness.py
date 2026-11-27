"""反向验证工具：逐个「破坏」实现，确认对应用例会失败。

**为什么需要这个工具**：一个永远绿的测试可能什么也没验证。
本项目已多次出现「测试通过但缺陷仍在」的情况
（见 ``docs/遗留问题.md`` 的 L-07a / L-25：单元测试全绿、真跑才暴露），
因此这里把「反向验证」固化成可重复执行的工具。

**做法**：对实现打一个**语义等价于目标缺陷**的补丁，
确认对应用例**确实失败**，然后恢复实现。
每个用例都必须能锁住它声称要锁的缺陷。

**一次破坏可能需要多处同时改**（例如「让 settings.py 也参与统计」
需要同时改文件筛选与消费判定），因此用**编辑列表**而不是单对。

**锚点失配会显式 SKIP 而不是静默跳过**——
静默跳过等于「没验证过却显示通过」，那正是本工具要消灭的模式。

用法::

    python scripts/verify_test_effectiveness.py

退出码 0 = 全部用例都能锁住对应缺陷；1 = 有用例锁不住。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/aicrypto/config/dependency_audit.py"

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

MUTATIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "test_alias_variable_counts_as_consumer",
        "别名变量追踪失效（cfg = get_settings() 不再被识别）",
        [
            (
                '            if name not in ("get_settings", "resolve_settings"):\n                continue',
                "            if True:\n                continue",
            )
        ],
    ),
    (
        "test_instance_attribute_counts_as_consumer",
        "实例属性追踪失效（self._settings.X 不再被识别）",
        [
            (
                "elif (\n                isinstance(base, ast.Attribute)",
                "elif False and (\n                isinstance(base, ast.Attribute)",
            )
        ],
    ),
    (
        "test_property_accessor_counts_as_consumer",
        "property 追踪失效（cfg.allowed_ips 不再算消费方）",
        [("elif node.attr in property_map:", "elif False:")],
    ),
    (
        "test_check_catches_unconsumed_dependency",
        "零消费依赖不再被拦",
        [("if usage.distribution in allowed:", "if True:")],
    ),
    (
        "test_check_ignores_test_only_usage",
        "「仅测试在用」也算已消费",
        [
            (
                "return bool(self.runtime_imports) or bool(self.other_evidence)",
                "return bool(self.runtime_imports) or bool(self.other_evidence) or bool(self.test_imports)",
            )
        ],
    ),
    (
        "test_ast_import_detection_ignores_comments_and_strings",
        "退回「文本也算 import」的行为",
        [
            (
                '        modules.add(alias.name.split(".")[0])',
                '        modules.add(alias.name.split(".")[0])\n'
                "        import re as _re\n"
                '        for _m in _re.finditer(r"import\\s+([A-Za-z_][A-Za-z0-9_]*)", '
                "path.read_text(encoding='utf-8')):\n"
                "            modules.add(_m.group(1))",
            )
        ],
    ),
    (
        "test_ast_import_detection_handles_relative_imports",
        "相对导入被当成第三方依赖",
        [
            (
                "            if node.level and node.level > 0:\n"
                "                # 相对导入（from . import x）：模块名是项目内的，不是第三方依赖\n"
                "                continue",
                "            if False:\n                pass",
            )
        ],
    ),
    (
        "test_whitelist_entry_without_reason_is_rejected",
        "允许无原因的白名单条目",
        [("if not entry.reason.strip():", "if False:")],
    ),
    (
        "test_whitelist_has_no_stale_entries",
        "不再检查依赖侧「白名单已过期」",
        [("if entry.key in consumed_dists:", "if False:")],
    ),
    (
        "test_stale_whitelist_entry_is_reported",
        "不再报告「白名单已过期」（合成样本）",
        [("if entry.key in consumed_dists:", "if False:")],
    ),
    (
        "test_no_dependency_is_only_used_by_tests",
        "不再报告「仅测试在用」的依赖（仓库级）",
        [
            (
                "        if usage.test_imports:\n"
                "            detail = f\"（仅测试在用：{', '.join(usage.test_imports[:3])}）\"",
                "        if False:\n"
                "            detail = f\"（仅测试在用：{', '.join(usage.test_imports[:3])}）\"",
            )
        ],
    ),
    (
        "test_test_only_dependency_is_reported",
        "「仅测试在用」不再被标记为 test_only（属性层）",
        [
            (
                "        return not self.is_consumed and bool(self.test_imports)",
                "        return False",
            )
        ],
    ),
    (
        "test_toolchain_distributions_are_excluded",
        "不再排除工具链（仓库级）",
        [("if dist in TOOLCHAIN_DISTRIBUTIONS or dist in seen:", "if dist in seen:")],
    ),
    (
        "test_toolchain_excluded_from_audit",
        "不再排除工具链（合成样本）",
        [("if dist in TOOLCHAIN_DISTRIBUTIONS or dist in seen:", "if dist in seen:")],
    ),
    (
        "test_self_validation_is_not_a_consumer",
        "settings.py 内部的自校验被算作消费方",
        [_SETTINGS_INCLUDED, _SELF_IS_SETTINGS],
    ),
    (
        "test_self_validation_inside_settings_is_not_a_consumer",
        "同上（仓库级）：settings.py 也参与消费方统计",
        [_SETTINGS_INCLUDED, _SELF_IS_SETTINGS],
    ),
    (
        "test_real_consumer_counts_as_consumer",
        "真实消费方不再被识别",
        [
            (
                "if node.attr.isupper():",
                "if node.attr.isupper() and node.attr != 'REALLY_USED':",
            )
        ],
    ),
    (
        "test_unrelated_class_member_is_not_a_consumer",
        "「任意类的大写属性」都算消费方（过宽实现）",
        [_ANY_BASE_IS_SETTINGS],
    ),
    (
        "test_unrelated_enum_member_is_not_a_consumer",
        "同上：过宽实现把非 Settings 类的同名成员误算成消费方",
        [_ANY_BASE_IS_SETTINGS],
    ),
    (
        "test_all_settings_fields_have_a_consumer",
        "同上：过宽实现会掩盖真实缺口",
        [_ANY_BASE_IS_SETTINGS],
    ),
]


def run_tests(names: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *names, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0, proc.stdout[-1500:]


def main() -> int:
    failures: list[str] = []
    original = TARGET.read_text(encoding="utf-8")
    try:
        for name, desc, edits in MUTATIONS:
            mutated = original
            missing = False
            for old, new in edits:
                if old not in mutated:
                    missing = True
                    break
                mutated = mutated.replace(old, new, 1)
            if missing:
                failures.append(f"[{name}] 破坏锚点未找到，无法验证：{desc}")
                print(f"SKIP {name}: 锚点未找到")
                continue
            TARGET.write_text(mutated, encoding="utf-8")
            ok, _out = run_tests([f"tests/unit/test_dependency_usage.py::{name}"])
            TARGET.write_text(original, encoding="utf-8")
            if ok:
                failures.append(f"[{name}] 破坏后测试**仍然通过**——该用例锁不住这个缺陷：{desc}")
                print(f"FAIL {name}: 破坏后仍通过 -> {desc}")
            else:
                print(f"OK   {name}: 破坏后正确失败 -> {desc}")
    finally:
        TARGET.write_text(original, encoding="utf-8")

    print()
    if failures:
        print("=== 验证失败 ===")
        for f in failures:
            print(" ", f)
        return 1
    print("=== 全部反向验证通过：每个用例都能锁住对应缺陷 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
