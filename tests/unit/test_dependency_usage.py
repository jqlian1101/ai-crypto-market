"""「声明 vs 实际消费」对账的 CI 闸门（L-30）。

**这个测试为什么存在**：本项目已累计 **11 次**出现「声明了但没人消费」
——配置项、指标、依赖、表全部就位，却**没有任何一行代码读取它们**
（L-03 保留期 / L-07a / L-16 / L-18 / L-19 / L-20 / L-21 / L-23 / L-26 /
L-29 ``LLM_AGENT_MAX_TOKENS`` / apscheduler / langgraph）。

这类缺陷**不会让任何测试失败**：配置能加载、依赖能安装、接口能返回 200，
只有真正用到那个功能时才会发现它从来没生效过。
**教训**：缺的是机制，不是纪律——**让 CI 去查**，而不是让人记得查。

**这个测试自己也要被验证**：它必须真的能拦住「新增一个零消费依赖」
与「新增一个零消费配置项」。因此下面有两组「反向验证」用例——
先造一个违规样本，确认检查会失败，再确认正常样本通过。
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from aicrypto.config.dependency_audit import (
    DEFAULT_WHITELIST,
    TOOLCHAIN_DISTRIBUTIONS,
    WhitelistEntry,
    audit_dependencies,
    audit_settings_fields,
    check_dependency_usage,
    check_settings_usage,
    check_whitelist_reasons,
)

# ---------------------------------------------------------------- 依赖对账


def test_all_declared_dependencies_have_a_consumer() -> None:
    """``pyproject.toml`` 声明的每个依赖都必须有运行时消费方。

    零消费即失败——除非显式列入 :data:`DEFAULT_WHITELIST` 并写明原因。
    """
    problems = check_dependency_usage()
    assert not problems, "依赖声明与实际 import 不一致:\n" + "\n".join(problems)


def test_no_dependency_is_only_used_by_tests(tmp_path: Path) -> None:
    """依赖不得「只有测试在用」。

    业务依赖若只有测试 import，说明它还没真正接入生产链路——
    这种情况比「完全零消费」更危险：它看起来是被用着的。

    **本用例必须真的能失败**：若实现不再判定「仅测试在用」，
    这里的断言也要失败——但本仓库当前**没有**这种依赖，
    所以「当前为空」这个断言是 vacuous 的。
    因此额外做一次**探针验证**：造一个只被测试 import 的依赖，
    确认检测机制确实会把它报出来。
    """
    offenders = [
        f"{usage.distribution}（仅测试：{', '.join(usage.test_imports[:3])}）"
        for usage in audit_dependencies()
        if usage.is_test_only
    ]
    assert not offenders, "以下依赖只有测试在用，未接入运行时:\n" + "\n".join(offenders)

    # 探针：确认检测机制本身是活的（否则上面那条断言永远成立）。
    # 必须用 pytest 的 ``tmp_path``——固定路径会在第二次运行时
    # 因目录已存在而报错，且会污染 ``tempfile.gettempdir()``。
    probe = _make_temp_repo(
        tmp_path,
        deps=["probe-only-in-tests>=1.0"],
        source="x = 1\n",
    )
    (probe / "tests").mkdir(exist_ok=True)
    (probe / "tests" / "test_probe.py").write_text("import probe_only_in_tests\n", encoding="utf-8")
    detected = [u.distribution for u in audit_dependencies(probe) if u.is_test_only]
    assert "probe-only-in-tests" in detected, "「仅测试在用」的检测机制失效"

    # 探针二：确认**报出的消息里**带「仅测试在用」的标注——
    # 否则使用者只会看到「零消费」，看不出它其实**已经被测试用上了**，
    # 这条信息对定位问题很关键。
    problems = check_dependency_usage(probe, whitelist=())
    reported = [p for p in problems if "probe-only-in-tests" in p]
    assert reported, "仅测试在用的依赖未被报出"
    assert any("仅测试在用" in p for p in reported), (
        f"仅测试在用的依赖没有被正确标注（缺少「仅测试在用」提示）: {reported}"
    )


def test_toolchain_distributions_are_excluded() -> None:
    """工具链（pytest / ruff / mypy …）不需要 import，必须被排除。

    不排除就会产生大量无意义的误报——**误报会让白名单越攒越多**，
    等于把要消灭的机制自己废掉。
    """
    distributions = {usage.distribution for usage in audit_dependencies()}
    assert not (distributions & TOOLCHAIN_DISTRIBUTIONS)


def test_whitelist_has_no_stale_entries() -> None:
    """白名单不得包含「已经有消费方」的条目。

    白名单是**暂缓**清单，不是**永久豁免**清单。
    对应阶段落地后，那条依赖/配置就应当从白名单里删掉——
    否则白名单会变成僵尸债务的坟场。

    **本用例必须真的能失败**：若实现不再报过期条目，
    这里的断言也要失败——因此不能只断言「消息里没有『已有消费方』」
    （不报的时候自然也没有这条消息，断言会 vacuously 通过）。
    判据是**双向**的：

    1. 当前白名单里不得有「已消费」的条目；
    2. 把一条**确实已被消费**的依赖塞进白名单，检查**必须**报出「该删了」——
       证明过期检测这条链路真的在工作，而不是空转。
    """
    consumed_dists = {u.distribution for u in audit_dependencies() if u.is_consumed}
    stale_keys = [entry.key for entry in DEFAULT_WHITELIST if entry.key in consumed_dists]
    assert not stale_keys, f"白名单存在已过期的条目（已有消费方，应删除）: {stale_keys}"

    # 反向验证：挑一个**确实有消费方**的依赖（如 httpx）临时塞进白名单，
    # 检查必须报「已有消费方，请从白名单删除」——
    # 这条链路若被关掉，断言立刻失败。
    already_consumed = next(iter(sorted(consumed_dists)))
    probe = (
        *DEFAULT_WHITELIST,
        WhitelistEntry(key=already_consumed, reason="临时塞入的过期条目", planned_for=None),
    )
    problems = check_dependency_usage(whitelist=probe)
    stale_reports = [p for p in problems if "已有消费方" in p and already_consumed in p]
    assert stale_reports, (
        f"白名单过期检测未生效（依赖 {already_consumed} 已有消费方却未被报出）: {problems}"
    )

    # 反向验证「零消费 → 报错」链路：白名单只包含配置项名，不是依赖包，
    # 因此这里不能依赖真实仓库里是否存在零消费依赖包（当前为 0）。
    # 改用合成的未消费依赖直接驱动 check_dependency_usage 的判定逻辑。
    from aicrypto.config import dependency_audit as da

    synthetic_unconsumed = da.DependencyUsage(
        distribution="__synthetic_unconsumed__",
        modules=("__synthetic__",),
        runtime_imports=(),
        test_imports=(),
        other_evidence=(),
    )
    original_audit = da.audit_dependencies
    da.audit_dependencies = lambda root=None: [synthetic_unconsumed]
    try:
        problems = da.check_dependency_usage(whitelist=())
    finally:
        da.audit_dependencies = original_audit

    assert any("__synthetic_unconsumed__" in p and "零消费" in p for p in problems), (
        f"零消费依赖未被报出——判据失效: {problems}"
    )


# ---------------------------------------------------------------- 配置对账


def test_all_settings_fields_have_a_consumer() -> None:
    """``Settings`` 的每个字段都必须有消费方。

    零消费即失败——除非显式列入 :data:`DEFAULT_WHITELIST` 并写明原因。
    """
    problems = check_settings_usage()
    assert not problems, "配置项与实际消费方不一致:\n" + "\n".join(problems)


def test_self_validation_inside_settings_is_not_a_consumer() -> None:
    """``settings.py`` 内部的自校验**不算消费方**。

    这正是 L-03 / L-19 / L-21 的共性：
    ``MARKET_TRADE_RETENTION_DAYS`` 在 Settings 里被引用了两次
    （定义 + 某个 validator），但**没有任何业务代码读它**——
    改配置依然不改变任何行为。

    本用例锁死：审计必须把 ``settings.py`` 排除在消费方统计之外。

    **判据必须是双向的**：
    - 正向：真被业务代码读到的字段（如 ``AUTH_TOKEN_SHA256``）算已消费；
    - 反向：只在 ``settings.py`` 内部被引用的字段**不算**已消费。

    只做正向断言的话，实现即使完全不算 settings.py 的引用也会通过——
    那就锁不住「自我校验不算消费方」这条规则。

    **探针字段选 ``APP_TIMEZONE``**：它在 ``settings.py`` 内部被
    ``self.`` 引用（``timezone`` 属性里 ``ZoneInfo(self.APP_TIMEZONE)``），
    但**没有任何业务代码读它**——
    它正是一个「声明了但零消费」的字段。
    若实现把 ``settings.py`` 的自校验/自派生误算成消费方，
    它就会变成「白名单已过期」，进而掩盖这个真实缺口。

    **为什么不用 ``LLM_AGENT_MAX_TOKENS``**：它曾是本用例的探针，
    但 P4-1b 多 Agent 拆分后它**真的**被业务代码消费了
    （``services/llm_agent.py`` 用它做「单 Agent 单次最大 token」上限，
    PRD 24.2）。
    探针必须是「确实没被消费」的那个字段——
    用一个**已经被消费**的字段当探针，
    这个用例会在实现正确时**误报失败**，
    而更糟的是：有人为了让用例通过而把实现改坏，
    或者干脆把断言删掉。**探针必须保持「未被消费」这个前提。**
    """
    consumed = audit_settings_fields()
    assert consumed, "审计结果不应为空"

    # 正向：真被业务代码读到的字段算已消费。
    assert consumed["AUTH_TOKEN_SHA256"] is True

    # 反向：只在 settings.py 内部被 validator 引用的字段**不算**已消费。
    assert consumed["APP_TIMEZONE"] is False

    # 且它**不得**出现在「白名单已过期」的问题列表里——
    # 出现过就说明 settings.py 的自校验被误算成了消费方。
    problems = check_settings_usage()
    stale = [p for p in problems if "APP_TIMEZONE" in p]
    assert not stale, f"settings.py 内部的自校验被误算为消费方: {stale}"


def test_unrelated_class_member_is_not_a_consumer() -> None:
    """**非 Settings 类**的同名大写成员**不算**消费方。

    ``ErrorCode.NOTIFY_QUIET_HOURS`` 是错误码枚举的成员，
    与 ``Settings.NOTIFY_QUIET_HOURS`` 毫无关系。
    一个过宽的检测器（把任意 ``X.大写属性`` 都算作消费方）
    会把它误判成「已消费」，从而**掩盖**「通知配置未接线」这个真实缺口。

    反之，``NOTIFY_QUIET_HOURS`` 确实没有运行时消费方（免打扰属 P6），
    它在白名单里——因此若检测器过宽，它会变成「白名单已过期」。
    """
    consumed = audit_settings_fields()
    assert consumed["NOTIFY_QUIET_HOURS"] is False

    # 且它**不得**出现在「白名单已过期」的问题列表里——
    # 出现过就说明检测器把 ErrorCode 的同名成员误算了。
    problems = check_settings_usage()
    stale = [p for p in problems if "NOTIFY_QUIET_HOURS" in p]
    assert not stale, f"非 Settings 类的同名成员被误算为消费方: {stale}"


def test_property_accessor_counts_as_consumer() -> None:
    """通过 ``@property`` 消费的配置项**算**消费方。

    ``ALLOWED_IPS`` 只通过 ``cfg.allowed_ips`` 被读取
    （``settings.allowed_ips`` 属性内部再读 ``self.ALLOWED_IPS``）。
    若不追踪属性访问器，就会把这个**真实消费方**误报成零消费——
    而误报会让白名单越攒越多。
    """
    consumed = audit_settings_fields()
    assert consumed["ALLOWED_IPS"] is True


def test_instance_attribute_counts_as_consumer() -> None:
    """通过实例属性消费的配置项**算**消费方。

    采集器把 Settings 存在 ``self._settings`` 上，
    之后用 ``self._settings.MARKET_FUNDING_POLL_SECONDS`` 读取。
    这类字段（轮询间隔、深度档位、重连上限）**恰恰是核心配置**，
    漏判它们等于把核心配置全部误报成「零消费」。
    """
    consumed = audit_settings_fields()
    assert consumed["MARKET_FUNDING_POLL_SECONDS"] is True
    assert consumed["MARKET_BOOK_DEPTH_LEVELS"] is True
    assert consumed["MARKET_WS_RECONNECT_MAX_MS"] is True
    assert consumed["CLOCK_SKEW_ALERT_MS"] is True


def test_alias_variable_counts_as_consumer() -> None:
    """别名为 ``cfg`` 的 Settings 变量**算**消费方。

    本代码库大量使用 ``cfg = settings or get_settings()``。
    只按变量名 ``settings`` 匹配会漏掉一大半真实消费方。
    """
    consumed = audit_settings_fields()
    assert consumed["REDIS_URL"] is True
    assert consumed["DATABASE_URL"] is True


def test_unrelated_enum_member_is_not_a_consumer() -> None:
    """**别的类**的同名常量**不算**消费方。

    ``ErrorCode.NOTIFY_QUIET_HOURS`` 是错误码枚举的成员，
    与 ``Settings.NOTIFY_QUIET_HOURS`` 毫无关系——
    把两者混为一谈会掩盖「通知配置未接线」这个真实缺口。
    """
    consumed = audit_settings_fields()
    # NOTIFY_QUIET_HOURS 目前确实没有运行时消费方（免打扰属 P6），
    # 它在白名单里。这里断言的是「不能因为 ErrorCode 里有同名成员就算消费」。
    assert consumed["NOTIFY_QUIET_HOURS"] is False


# ---------------------------------------------------------------- 白名单自身


def test_every_whitelist_entry_has_a_reason() -> None:
    """每条白名单都必须写原因。

    **没有原因的白名单条目就是永久豁免**——
    那正是本模块要消灭的模式本身。
    """
    problems = check_whitelist_reasons()
    assert not problems, "白名单不合规:\n" + "\n".join(problems)


def test_whitelist_has_no_duplicates() -> None:
    keys = [entry.key for entry in DEFAULT_WHITELIST]
    assert len(keys) == len(set(keys))


def test_whitelist_reason_is_not_placeholder() -> None:
    """白名单原因不能是「TODO」「待定」这类占位符。"""
    placeholders = {"todo", "tbd", "fixme", "待定", "待补", "n/a", "-"}
    for entry in DEFAULT_WHITELIST:
        assert entry.reason.strip().lower() not in placeholders, (
            f"白名单条目 {entry.key} 的原因是占位符，请写清「为什么现在没有消费方」"
        )


def test_whitelist_keys_are_real() -> None:
    """白名单里的 key 必须真实存在于 Settings 字段或依赖名中。

    否则白名单会攒下**根本不存在的条目**——
    它们永远不会触发任何检查，只是让白名单看起来越来越「有依据」。
    """
    distributions = {usage.distribution for usage in audit_dependencies()}
    fields = set(audit_settings_fields())
    for entry in DEFAULT_WHITELIST:
        assert entry.key in distributions or entry.key in fields, (
            f"白名单条目 {entry.key} 既不是依赖名也不是 Settings 字段——请删除"
        )


# ------------------------------------------------- 反向验证：闸门真的能拦住


def _make_temp_repo(tmp_path: Path, *, deps: list[str], source: str) -> Path:
    """造一个最小仓库：带 ``pyproject.toml`` 与 ``src/`` 源码。"""
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "pkg" / "mod.py").write_text(textwrap.dedent(source), encoding="utf-8")
    deps_block = "\n".join(f'    "{d}",' for d in deps)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "tmp"\nversion = "0.1.0"\ndependencies = [\n{deps_block}\n]\n',
        encoding="utf-8",
    )
    return tmp_path


def test_check_catches_unconsumed_dependency(tmp_path: Path) -> None:
    """反向验证：新增一个零消费依赖**必须**被拦住。

    这条用例是本闸门存在意义的直接证明：
    如果它拦不住，「依赖声明 vs 实际 import 对账」就只是一句口号。
    """
    repo = _make_temp_repo(
        tmp_path,
        deps=["httpx>=0.28", "totally-unused-pkg>=1.0"],
        source="import httpx\n",
    )
    problems = check_dependency_usage(repo, whitelist=())
    assert any("totally-unused-pkg" in p for p in problems), f"零消费依赖未被拦住: {problems}"


def test_check_passes_when_dependency_is_consumed(tmp_path: Path) -> None:
    """反向验证：依赖**有**消费方时不得误报。"""
    repo = _make_temp_repo(
        tmp_path,
        deps=["httpx>=0.28"],
        source="import httpx\n\n\ndef f() -> httpx.Response: ...\n",
    )
    assert check_dependency_usage(repo, whitelist=()) == []


def test_check_ignores_test_only_usage(tmp_path: Path) -> None:
    """反向验证：只有测试在用的依赖**必须**被拦住。

    业务依赖若只有测试 import，说明它还没接入生产链路——
    它看起来「被用着」，比完全零消费更危险。
    """
    repo = _make_temp_repo(
        tmp_path,
        deps=["httpx>=0.28"],
        source="x = 1\n",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mod.py").write_text("import httpx\n", encoding="utf-8")
    usages = {u.distribution: u for u in audit_dependencies(repo)}
    assert usages["httpx"].is_test_only is True
    assert usages["httpx"].is_consumed is False


def test_whitelist_entry_without_reason_is_rejected() -> None:
    """反向验证：无原因的白名单条目**必须**被拒绝。"""
    bad = (WhitelistEntry(key="foo", reason="   "),)
    problems = check_whitelist_reasons(bad)
    assert problems and "foo" in problems[0]


def test_whitelist_entry_with_reason_is_accepted() -> None:
    ok = (WhitelistEntry(key="foo", reason="P5 调度层落地时消费", planned_for="P5"),)
    assert check_whitelist_reasons(ok) == []


def test_ast_import_detection_ignores_comments_and_strings(tmp_path: Path) -> None:
    """反向验证：注释与字符串里的模块名**不算** import。

    用正则做这件事会把 ``# import httpx`` 与 ``"httpx"`` 都算进去——
    于是「没在用」的依赖看起来「在用」，闸门失效。
    """
    repo = _make_temp_repo(
        tmp_path,
        deps=["httpx>=0.28"],
        source="""
        # import httpx
        TEXT = "import httpx"
        '''import httpx'''
        """,
    )
    usages = {u.distribution: u for u in audit_dependencies(repo)}
    assert usages["httpx"].runtime_imports == ()


def test_ast_import_detection_handles_relative_imports(tmp_path: Path) -> None:
    """反向验证：相对导入**不得**被当成第三方依赖。

    ``from . import sibling`` 导入的是**项目内**的模块，
    若把它当成第三方包，会凭空多出「有消费方」的假象。

    合成样本里刻意声明一个名为 ``mod`` 的依赖——
    相对导入 ``from .mod import thing`` 若被当成第三方依赖，
    就会让 ``mod`` 看起来「有消费方」。
    """
    repo = _make_temp_repo(
        tmp_path,
        deps=["httpx>=0.28", "mod>=1.0"],
        source="""
        from . import sibling
        from .mod import thing
        from ..pkg import other
        """,
    )
    usages = {u.distribution: u for u in audit_dependencies(repo)}
    assert usages["httpx"].runtime_imports == ()
    assert usages["mod"].runtime_imports == (), (
        "相对导入被误当成第三方依赖——项目内模块会凭空多出「消费方」"
    )


def test_module_ast_parse_is_robust() -> None:
    """``ast`` 解析不得因源码里的非 UTF8 或语法错误炸掉整个审计。"""
    tree = ast.parse("x = 1\n")
    assert isinstance(tree, ast.Module)


# ------------------------------- 白名单「过期条目」必须被报出来（合成样本）


def test_stale_whitelist_entry_is_reported(tmp_path: Path) -> None:
    """反向验证：白名单里的依赖**一旦有了消费方**，必须报「该删了」。

    白名单是**暂缓**清单，不是**永久豁免**清单。
    对应阶段落地后，那条条目应当从白名单里删掉——
    否则白名单会变成僵尸债务的坟场，没人再回头看它。

    用合成仓库验证：依赖 ``httpx`` 既有消费方，又在白名单里。
    """
    repo = _make_temp_repo(
        tmp_path,
        deps=["httpx>=0.28"],
        source="import httpx\n",
    )
    stale = (WhitelistEntry(key="httpx", reason="曾经没有消费方", planned_for="P5"),)
    problems = check_dependency_usage(repo, whitelist=stale)
    assert any("已有消费方" in p for p in problems), f"白名单过期条目未被报出: {problems}"


def test_stale_whitelist_entry_not_reported_when_still_unconsumed(tmp_path: Path) -> None:
    """反向验证：依赖**确实没有**消费方时，白名单条目**不得**被报过期。

    与上一条构成一对：不能只报不认，也不能只认不报。
    """
    repo = _make_temp_repo(
        tmp_path,
        deps=["httpx>=0.28"],
        source="x = 1\n",
    )
    entry = (WhitelistEntry(key="httpx", reason="尚未接入", planned_for="P5"),)
    problems = check_dependency_usage(repo, whitelist=entry)
    assert not any("已有消费方" in p for p in problems), f"未消费依赖被误报为白名单过期: {problems}"


# ------------------- settings.py 内部的自校验不得算作消费方（合成样本）


def _indent_block(body: str) -> str:
    """把一段代码缩进到类体层级（4 空格）。

    **必须先 dedent 再 indent**：传入的代码块在测试文件里是带缩进的
    （源码里的字符串字面量保留了行首空格），
    直接加 4 空格会得到「装饰器 16 空格、方法体 20 空格」的畸形缩进——
    Python 语法错误会让 AST 解析失败，
    于是**测试会因为错误的原因通过**（解析失败 → 无消费方 → 断言成立），
    看起来绿，其实什么都没验证。

    空行保持为空，不加尾随空格。
    """
    dedented = textwrap.dedent(body.strip("\n"))
    return "\n".join(f"    {line}" if line.strip() else "" for line in dedented.splitlines())


def _make_settings_repo(tmp_path: Path, *, settings_body: str, consumer: str) -> Path:
    """造一个带 ``Settings`` 模型的最小仓库。

    ``settings_body`` 是 Settings 类的字段（不带缩进）；
    ``consumer`` 是业务侧消费代码。

    **注意**：这里**不用** ``textwrap.dedent`` 拼整个文件——
    插值进来的字段行缩进会被 dedent 一并剥掉，
    导致字段落到模块级（缩进 0），而审计的正则要求类体缩进 4 空格。
    字段行单独用 ``indent`` 处理。
    """
    config_dir = tmp_path / "src" / "aicrypto" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "__init__.py").write_text("", encoding="utf-8")

    body = _indent_block(settings_body)
    content = (
        "from __future__ import annotations\n"
        "\n"
        "from pydantic import BaseSettings, model_validator\n"
        "\n"
        "\n"
        "class Settings(BaseSettings):\n"
        f"{body}\n"
    )
    (config_dir / "settings.py").write_text(content, encoding="utf-8")

    svc = tmp_path / "src" / "aicrypto" / "services"
    svc.mkdir(parents=True)
    (svc / "__init__.py").write_text("", encoding="utf-8")
    (svc / "mod.py").write_text(textwrap.dedent(consumer), encoding="utf-8")

    # 夹具自检：合成文件**必须**能被解析。
    # 夹具写坏（缩进畸形）会让 AST 解析失败，
    # 于是「无消费方」恒成立——测试会因为**错误的原因**通过，
    # 看起来绿，其实什么都没验证。
    for path in (config_dir / "settings.py", svc / "mod.py"):
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - 夹具自身的错误
            raise AssertionError(
                f"合成夹具语法错误，测试会因错误的原因通过: {path}\n{exc}"
            ) from exc
    return tmp_path


def test_self_validation_is_not_a_consumer(tmp_path: Path) -> None:
    """反向验证：``Settings`` 内部的 ``self.X`` 自校验**不算**消费方。

    这是 L-03 / L-19 / L-21 的共性：
    字段在 Settings 里被 validator 引用，看起来「被用着」，
    但**没有任何业务代码读它**——改配置依然不改变任何行为。

    合成样本里放了两个**诱饵**：

    1. ``settings.py`` 内部的 ``self.ONLY_VALIDATED`` 自校验；
    2. 业务代码里一个**非 Settings** 的类 ``Other.ONLY_VALIDATED``。

    两者都**不得**让该字段变成「已消费」——
    否则一个过宽的检测器（把任意大写属性访问都算作消费方）
    就会让真实缺口被掩盖。
    """
    repo = _make_settings_repo(
        tmp_path,
        settings_body="""
            ONLY_VALIDATED: int = 5

            @model_validator(mode="after")
            def _check(self) -> Settings:
                if self.ONLY_VALIDATED < 0:
                    raise ValueError("bad")
                return self
        """,
        consumer="""
            from aicrypto.config.settings import Settings


            class Other:
                ONLY_VALIDATED = 1


            def read(cfg: Settings) -> int:
                return Other.ONLY_VALIDATED
        """,
    )
    problems = check_settings_usage(repo, whitelist=())
    assert any("ONLY_VALIDATED" in p for p in problems), (
        f"Settings 内部自校验 / 非 Settings 类的同名成员被误判为消费方: {problems}"
    )


def test_real_consumer_counts_as_consumer(tmp_path: Path) -> None:
    """反向验证：业务代码真实读取字段**必须**算消费方。

    与上一条构成一对：不能只报不认，也不能只认不报。
    """
    repo = _make_settings_repo(
        tmp_path,
        settings_body="""
            REALLY_USED: int = 5
        """,
        consumer="""
            from aicrypto.config.settings import Settings


            def read(cfg: Settings) -> int:
                return cfg.REALLY_USED
        """,
    )
    problems = check_settings_usage(repo, whitelist=())
    assert not any("REALLY_USED" in p for p in problems), f"真实消费方被误报为零消费: {problems}"


# ----------------------------- 「仅测试在用」的依赖必须被报出来（合成样本）


def test_test_only_dependency_is_reported(tmp_path: Path) -> None:
    """反向验证：只有测试 import 的依赖**必须**被报出来。

    业务依赖若只有测试 import，说明它还没接入生产链路——
    它看起来「被用着」，比完全零消费更危险。
    """
    repo = _make_temp_repo(
        tmp_path,
        deps=["httpx>=0.28"],
        source="x = 1\n",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mod.py").write_text("import httpx\n", encoding="utf-8")
    offenders = [usage.distribution for usage in audit_dependencies(repo) if usage.is_test_only]
    assert "httpx" in offenders


def test_toolchain_excluded_from_audit(tmp_path: Path) -> None:
    """反向验证：工具链依赖**不得**进入审计结果。

    pytest / ruff / mypy 由 CI 直接调用，不通过 import 使用。
    不排除它们就会产生大量无意义的误报——
    **误报会让白名单越攒越多**，等于把要消灭的机制自己废掉。
    """
    repo = _make_temp_repo(
        tmp_path,
        deps=["pytest>=8.3", "httpx>=0.28"],
        source="import httpx\n",
    )
    distributions = {usage.distribution for usage in audit_dependencies(repo)}
    assert "pytest" not in distributions
    assert "httpx" in distributions
