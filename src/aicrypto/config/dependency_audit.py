"""依赖与配置项的「声明 vs 实际消费」对账（PRD 36.4 ``config-check`` job）。

**为什么需要这个模块**：本项目已累计 **11 次**出现「声明了但没人消费」
——配置项、指标、依赖、表全部就位，却**没有任何一行代码读取它们**
（L-03 保留期 / L-07a / L-16 / L-18 / L-19 / L-20 / L-21 / L-23 / L-26 /
L-29 ``LLM_AGENT_MAX_TOKENS`` / apscheduler）。

这类缺陷**不会让任何测试失败**：配置能加载、依赖能安装、接口能返回 200，
只有真正用到那个功能时才会发现它从来没生效过。

**判据**：判断一项配置/依赖是否真的生效，要 grep **消费方**
（谁在读那个值），而不是看**配套设施**（默认值、约束、文档是否齐全）。

**三条设计纪律**：

1. **零消费即报错**——除非显式列入白名单。
2. **白名单必须写原因**——每条都要写清「为什么现在没有消费方」，
   否则白名单会退化成「永远豁免」的垃圾桶（这正是要消灭的模式本身）。
3. **白名单条目必须有归属**——写明由哪个阶段消费，
   让「暂缓」变成有到期日、可被追责的承诺，而不是无限期挂起。

**重要**：白名单**不是**「永久豁免名单」。每条都带 ``planned_for`` 阶段标记；
当对应阶段落地时，那条配置应当**从白名单里删掉**（因为它已经有消费方了），
否则白名单会变成僵尸债务的坟场。:func:`check_dependency_usage` 会主动
检查并报告这类「白名单已过期」的条目。

**误报的代价**：这个检查一旦误报，白名单就会越攒越多，
最后等于把要消灭的机制自己废掉。因此本模块**必须精确**：

- 依赖消费方用 **AST** 判定（不是正则）——正则会把注释、字符串、
  其它类的同名常量都算进来；
- 配置消费方用**类型感知的 AST** 判定——本代码库把 ``Settings``
  别名成 ``cfg``，且大量配置通过 ``@property`` 消费，
  只按变量名 ``settings`` 匹配会漏掉一大半真实消费方。
"""

from __future__ import annotations

import ast
import os
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from aicrypto.config.settings import PROJECT_ROOT

#: 参与对账的源码目录（相对仓库根）
SOURCE_DIRS = ("src", "scripts", "migrations")

#: 测试目录——**不算运行时消费方**，但单独统计（用于判断依赖该放哪组）
TEST_DIR = "tests"

#: ``pyproject.toml`` 中的发行名 → 实际 import 名
#:
#: 键是 ``pyproject.toml`` 里的 **distribution name**（PyPI 名），
#: 值是代码里实际 ``import`` 的**模块名**——两者经常不同
#: （``psycopg[binary]`` → ``psycopg``、``ulid-py`` → ``ulid``、
#: ``python-multipart`` → ``multipart``、``prometheus-client`` → ``prometheus_client``）。
#:
#: **这张表必须显式维护**，不能靠字符串替换推导：
#: 靠推导会把 ``psycopg[binary]`` 误判成模块名 ``psycopg[binary]``，
#: 于是「有消费方」的依赖被误报成「零消费」。
DISTRIBUTION_TO_MODULE: dict[str, tuple[str, ...]] = {
    "fastapi": ("fastapi",),
    "uvicorn": ("uvicorn",),
    "pydantic": ("pydantic",),
    "pydantic-settings": ("pydantic_settings",),
    "python-multipart": ("multipart",),
    "sqlalchemy": ("sqlalchemy",),
    "alembic": ("alembic",),
    "psycopg": ("psycopg",),
    "redis": ("redis",),
    "structlog": ("structlog",),
    "prometheus-client": ("prometheus_client",),
    "apscheduler": ("apscheduler",),
    "httpx": ("httpx",),
    "websockets": ("websockets",),
    "langgraph": ("langgraph",),
    "ulid-py": ("ulid",),
    "tenacity": ("tenacity",),
    "tomli-w": ("tomli_w",),
}

#: 属于「工具链」而非「运行时依赖」的发行名——不需要有 import。
#:
#: 这些是测试/构建/类型检查工具，由 CI 直接调用（``pytest`` / ``ruff`` / ``mypy``），
#: 不通过 ``import`` 使用。**不排除它们就会产生大量无意义的误报**。
TOOLCHAIN_DISTRIBUTIONS = frozenset(
    {
        "pytest",
        "pytest-asyncio",
        "pytest-cov",
        "freezegun",
        "ruff",
        "mypy",
        "types-requests",
    }
)

#: 驱动类依赖：以「URL 方言名」形式被引用，不需要 ``import``。
#:
#: SQLAlchemy 通过 ``postgresql+psycopg://`` 这样的 URL 方言串加载驱动，
#: 代码里**从不出现** ``import psycopg``。若不识别这一形态，
#: 就会把**正在使用的驱动**误报成「零消费」——
#: 而误报会让白名单越攒越多，等于把要消灭的机制自己废掉。
DIALECT_REFERENCE_PATTERNS: dict[str, tuple[str, ...]] = {
    "psycopg": ("postgresql+psycopg",),
}

#: 命令行入口类依赖：以 ``python -m <module>`` 形式运行，不 ``import``。
#:
#: ``uvicorn`` 是 ASGI 服务器，由启动命令调用——
#: ``python -m uvicorn aicrypto.api.app:create_app --factory``。
#: 它的「消费方」就是**启动命令本身**。
CLI_ENTRYPOINT_PATTERNS: dict[str, tuple[str, ...]] = {
    "uvicorn": ("python -m uvicorn", "uvicorn aicrypto"),
}


@dataclass(frozen=True, slots=True)
class DependencyUsage:
    """一个发行包在代码里的消费情况。"""

    distribution: str
    modules: tuple[str, ...]
    #: 命中 ``import`` 的运行时文件（相对仓库根）
    runtime_imports: tuple[str, ...]
    #: 命中 ``import`` 的测试文件（相对仓库根）
    test_imports: tuple[str, ...]
    #: 非 import 形态的消费证据（方言串 / 命令行入口）
    other_evidence: tuple[str, ...]

    @property
    def is_consumed(self) -> bool:
        """是否至少有一处消费证据。

        **只看运行时消费**——测试里的 import 不算运行时消费方：
        ``pytest`` 这类库本来就在 ``[dev]`` 组，而业务依赖若只有测试在用，
        说明它还没真正接入生产链路。
        """
        return bool(self.runtime_imports) or bool(self.other_evidence)

    @property
    def is_test_only(self) -> bool:
        """是否**只有**测试在用。"""
        return not self.is_consumed and bool(self.test_imports)


@dataclass(frozen=True, slots=True)
class WhitelistEntry:
    """一条白名单豁免。

    **每条都必须有非空原因**——这是白名单不至于退化成垃圾桶的唯一保障。
    """

    key: str
    reason: str
    #: 由哪个阶段消费（``P5`` / ``P6`` / ``P7`` …），或 ``None`` 表示「无计划」
    planned_for: str | None = None


#: 零消费豁免清单。
#:
#: **每条都是「暂缓」，不是「豁免」**——对应阶段落地后必须删掉该条。
#:
#: 排序按阶段。理由统一写清「为什么现在没有消费方」。
DEFAULT_WHITELIST: tuple[WhitelistEntry, ...] = (
    # ---- P5 调度层 ----
    WhitelistEntry(
        "apscheduler",
        reason="P5 调度层：采用纯函数 cron 解析，暂不常驻，apscheduler 可选用于后期守护进程化",
        planned_for="P5",
    ),
    # ---- P6 通知 ----
    WhitelistEntry(
        "WECOM_APP_ENABLED",
        reason="企业微信渠道为 P1，PRD 22.5 明确暂不做",
        planned_for=None,
    ),
    WhitelistEntry(
        "WECOM_CORP_ID",
        reason="企业微信渠道为 P1，PRD 22.5 明确暂不做",
        planned_for=None,
    ),
    WhitelistEntry(
        "WECOM_CORP_SECRET",
        reason="企业微信渠道为 P1，PRD 22.5 明确暂不做",
        planned_for=None,
    ),
    WhitelistEntry(
        "WECOM_AGENT_ID",
        reason="企业微信渠道为 P1，PRD 22.5 明确暂不做",
        planned_for=None,
    ),
    WhitelistEntry(
        "WECOM_TO_USER",
        reason="企业微信渠道为 P1，PRD 22.5 明确暂不做",
        planned_for=None,
    ),
    WhitelistEntry(
        "WECOM_TO_PARTY",
        reason="企业微信渠道为 P1，PRD 22.5 明确暂不做",
        planned_for=None,
    ),
    WhitelistEntry(
        "WECOM_TO_TAG",
        reason="企业微信渠道为 P1，PRD 22.5 明确暂不做",
        planned_for=None,
    ),
    WhitelistEntry(
        "WECOM_APP_MSG_TYPE",
        reason="企业微信渠道为 P1，PRD 22.5 明确暂不做",
        planned_for=None,
    ),
    WhitelistEntry(
        "WECOM_BOT_ENABLED",
        reason="企业微信渠道为 P1，PRD 22.5 明确暂不做",
        planned_for=None,
    ),
    WhitelistEntry(
        "WECOM_BOT_WEBHOOK_URL",
        reason="企业微信渠道为 P1，PRD 22.5 明确暂不做",
        planned_for=None,
    ),
    WhitelistEntry(
        "NOTIFY_DAILY_QUOTA",
        reason="账户级配额（PRD 22.4.4.2）接入真实链路时消费；当前由调用方注入",
        planned_for="P6",
    ),
    WhitelistEntry(
        "NOTIFY_CRITICAL_BYPASS_QUIET",
        reason="免打扰时段链路未接通（PRD 22.4.4.2）；NOTIFY_QUIET_HOURS 本身亦未消费",
        planned_for="P6",
    ),
    WhitelistEntry(
        "NOTIFY_QUIET_HOURS",
        reason="免打扰按用户时区判定未接通（PRD 22.4.4.2）",
        planned_for="P6",
    ),
    WhitelistEntry(
        "NOTIFY_DELIVERY_MODE",
        reason="三级投递模式（PRD 22.4.4.1）未接入；当前只有广播一种",
        planned_for="P6",
    ),
    WhitelistEntry(
        "NOTIFY_QUOTA_COUNTING_MODE",
        reason="配额计数模式（PRD 22.4.4.2）未接入",
        planned_for="P6",
    ),
    WhitelistEntry(
        "NOTIFY_DEDUP_TTL_SECONDS",
        reason="去重 TTL 写死在 domain/anomaly/dedup.py 的 DEDUP_TTL，配置项未接线",
        planned_for="P6",
    ),
    WhitelistEntry(
        "NOTIFY_COOLDOWN_CRITICAL_SECONDS",
        reason="冷却窗口写死在 domain/anomaly/dedup.py 的 COOLDOWN_*，配置项未接线",
        planned_for="P6",
    ),
    WhitelistEntry(
        "NOTIFY_COOLDOWN_WARN_SECONDS",
        reason="冷却窗口写死在 domain/anomaly/dedup.py 的 COOLDOWN_*，配置项未接线",
        planned_for="P6",
    ),
    WhitelistEntry(
        "NOTIFY_AI_MERGE_WINDOW_SECONDS",
        reason="合并等待窗口 30s（PRD 22.4.4.1）未实现",
        planned_for="P6",
    ),
    WhitelistEntry(
        "NOTIFY_WECOM_TOKEN_REFRESH_MARGIN_SECONDS",
        reason="企业微信渠道为 P1，PRD 22.5 明确暂不做",
        planned_for=None,
    ),
    WhitelistEntry(
        "NOTIFY_CHANNEL_PRIORITY",
        reason="渠道优先级（PRD 22.4.4.2）未接入；当前只发飞书",
        planned_for="P6",
    ),
    WhitelistEntry(
        "NOTIFY_SEVERITY_ROUTING",
        reason="按严重度路由到不同渠道（PRD 22.4.4.2）未接入",
        planned_for="P6",
    ),
    # ---- P5 调度层（配置项） ----
    # SCHEDULER_ENABLED: 已被 scripts/run_scheduler.py 消费，从白名单删除
    WhitelistEntry(
        "SCHEDULER_TIMEZONE",
        reason="P5 调度层：计划扩展多时区支持时消费",
        planned_for="P5",
    ),
    WhitelistEntry(
        "SCHEDULER_MAX_BACKFILL_SECONDS",
        reason="P5 调度层尚未开工（补跑机制 PRD 23.1.4）",
        planned_for="P5",
    ),
    WhitelistEntry(
        "SCHEDULER_LOCK_MODE",
        reason="P5 调度层尚未开工（任务锁 PRD 23.1.4）",
        planned_for="P5",
    ),
    WhitelistEntry(
        "SCHEDULER_WORKER_POOL",
        reason="P5 调度层尚未开工",
        planned_for="P5",
    ),
    WhitelistEntry(
        "ANOMALY_SCAN_INTERVAL_SECONDS",
        reason="异动扫描定时化未实现（当前靠 scripts/run_anomaly_scan.py 手动执行）",
        planned_for="P5",
    ),
    WhitelistEntry(
        "ANOMALY_EVAL_HORIZONS",
        reason="异动评估时长的判定口径未实现（PRD 22.6 / P7 评估）",
        planned_for="P7",
    ),
    WhitelistEntry(
        "ANOMALY_RULE_CONFIG_PATH",
        reason="规则配置路径写死在 config/anomaly_rules.py，配置项未接线",
        planned_for="P5",
    ),
    WhitelistEntry(
        "ANOMALY_RULES_DISABLED",
        reason="禁用规则实际读 base.toml 的 [anomaly.rules].disabled_rules；本配置项未接线",
        planned_for="P5",
    ),
    # ---- P2 数据保留 ----
    WhitelistEntry(
        "MARKET_TRADE_RETENTION_DAYS",
        reason="数据保留与清理未实现（L-03）；PRD 27.4 要求并入 scheduled_jobs 的 retention 任务",
        planned_for="P5",
    ),
    WhitelistEntry(
        "RETENTION_ENABLED",
        reason="数据保留与清理未实现（L-03 / L-04）",
        planned_for="P5",
    ),
    # ---- P7 评估口径 ----
    WhitelistEntry(
        "LLM_DAILY_TOKEN_BUDGET",
        reason="TODO 已明确「暂不做成本优化，仅保留成本分析」（PRD 24.2 延后清单）",
        planned_for=None,
    ),
    WhitelistEntry(
        "LLM_DAILY_COST_BUDGET_USD",
        reason="TODO 已明确「暂不做成本优化，仅保留成本分析」（PRD 24.2 延后清单）",
        planned_for=None,
    ),
    # ---- P9 / 运维 ----
    WhitelistEntry(
        "BACKUP_ENABLED",
        reason="备份策略未实现（PRD 27.4 / 37.7）",
        planned_for="P9",
    ),
    WhitelistEntry(
        "BACKUP_DIR",
        reason="备份策略未实现（PRD 27.4 / 37.7）",
        planned_for="P9",
    ),
    WhitelistEntry(
        "BACKUP_RETENTION_DAYS",
        reason="备份策略未实现（PRD 27.4 / 37.7）",
        planned_for="P9",
    ),
    WhitelistEntry(
        "BACKUP_RESTORE_DRILL_ENABLED",
        reason="恢复演练未实现（PRD 27.4）",
        planned_for="P9",
    ),
    WhitelistEntry(
        "METRICS_ENABLED",
        reason="独立指标服务未拆分；指标当前随 /metrics 端点暴露",
        planned_for="P9",
    ),
    WhitelistEntry(
        "METRICS_BIND",
        reason="独立指标服务未拆分；指标当前随 /metrics 端点暴露",
        planned_for="P9",
    ),
    WhitelistEntry(
        "METRICS_AUTH_REQUIRED",
        reason="独立指标服务未拆分；指标当前随 /metrics 端点暴露",
        planned_for="P9",
    ),
    WhitelistEntry(
        "FREQTRADE_API_BASE_URL",
        reason="Freqtrade 为 Phase 7–8，明确延后",
        planned_for=None,
    ),
    WhitelistEntry(
        "FREQTRADE_API_USERNAME",
        reason="Freqtrade 为 Phase 7–8，明确延后",
        planned_for=None,
    ),
    WhitelistEntry(
        "FREQTRADE_API_PASSWORD",
        reason="Freqtrade 为 Phase 7–8，明确延后",
        planned_for=None,
    ),
    WhitelistEntry(
        "FREQTRADE_IMAGE_TAG",
        reason="Freqtrade 为 Phase 7–8，明确延后",
        planned_for=None,
    ),
    # ---- 公网回调 / 短链 ----
    WhitelistEntry(
        "PUBLIC_CALLBACK_ENABLED",
        reason="公网回调入口未启用（PRD 34.6，企微回调入口一并延后）",
        planned_for=None,
    ),
    WhitelistEntry(
        "PUBLIC_CALLBACK_PORT",
        reason="公网回调入口未启用（PRD 34.6）",
        planned_for=None,
    ),
    WhitelistEntry(
        "PUBLIC_CALLBACK_TOKEN",
        reason="公网回调入口未启用（PRD 34.6）",
        planned_for=None,
    ),
    # ---- 前端 / 分享 ----
    WhitelistEntry(
        "DASHBOARD_PUBLIC_BASE_URL",
        reason="短链基准地址未接线（分享页 /s/[id] 尚未对外发布）",
        planned_for="P8",
    ),
    WhitelistEntry(
        "FRONTEND_URL",
        reason="前端基址未接线（后端未向分享页输出绝对 URL）",
        planned_for="P8",
    ),
    # ---- 采集参数 ----
    WhitelistEntry(
        "MARKET_BACKFILL_ENABLED",
        reason="补数开关未接线；缺口检测已实现但补数走脚本参数",
        planned_for="P5",
    ),
    WhitelistEntry(
        "MARKET_BACKFILL_MAX_KLINES",
        reason="补数上限未接线；补数走脚本参数",
        planned_for="P5",
    ),
    # ---- 鉴权 ----
    WhitelistEntry(
        "AUTH_RATE_LIMIT_PER_MIN",
        reason="鉴权限流未实现（PRD 34.3）",
        planned_for="P9",
    ),
    # ---- 通知渠道开关 ----
    WhitelistEntry(
        "FEISHU_ENABLED",
        reason="投递链路未读该开关——当前只要 WEBHOOK_URL 非空就发；开关未接线",
        planned_for="P6",
    ),
    # ---- 用户时区 ----
    WhitelistEntry(
        "APP_TIMEZONE",
        reason="用户时区未接线；免打扰按用户时区判定属 P6，settings.timezone 属性亦无消费方",
        planned_for="P6",
    ),
)


def _read_pyproject(root: Path) -> dict[str, Any]:
    with (root / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _distribution_name(raw: str) -> str:
    """从依赖声明串里取出发行名。

    ``"psycopg[binary]>=3.2"`` → ``"psycopg"``；``"fastapi>=0.115"`` → ``"fastapi"``。
    """
    return re.split(r"[<>=!~ \[\]]", raw.strip(), maxsplit=1)[0].strip().lower()


def _iter_python_files(root: Path, *directories: str) -> list[Path]:
    files: list[Path] = []
    for directory in directories:
        base = root / directory
        if not base.exists():
            continue
        files.extend(sorted(base.rglob("*.py")))
    return files


def _module_imports(path: Path) -> set[str]:
    """用 AST 提取该文件 import 的**顶层**模块名。

    **用 AST 而不是正则**：正则会把 ``from a.b import c`` 里的 ``a.b``
    与注释、字符串里的同名文本都算进来——**误报会让白名单越攒越多**，
    等于把要消灭的机制自己废掉。
    """
    modules: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - 源码损坏时由 CI 的 ruff/pytest 报
        return modules
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # 相对导入（from . import x）：模块名是项目内的，不是第三方依赖
                continue
            if node.module:
                modules.add(node.module.split(".")[0])
    return modules


def _iter_repo_text_files(root: Path) -> list[Path]:
    """仓库内所有可能包含「方言串 / 命令行入口」的文本文件。

    排除 ``.git`` / ``.venv`` / ``node_modules`` 等噪声目录。

    **单次目录遍历**（不是每个 glob 模式各遍历一次）：
    13 个 ``rglob`` 各扫一遍全仓，在 Windows 上要 4.5 秒——
    放进 CI 的每次提交里是不可接受的。
    单次 ``os.walk`` 只需 0.2 秒。
    """
    skip_dirs = {".git", ".venv", "node_modules", "__pycache__", ".ruff_cache", ".mypy_cache"}
    suffixes = {".md", ".toml", ".yml", ".yaml", ".cfg", ".ini", ".sh", ".txt"}
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() in suffixes or filename.startswith("Dockerfile"):
                found.append(path)
    return found


def audit_dependencies(root: Path | None = None) -> list[DependencyUsage]:
    """对账 ``pyproject.toml`` 的运行时依赖与代码里的实际消费证据。

    返回每个运行时依赖的消费情况。

    **带缓存**：同一进程内对同一仓库的重复调用不重复扫全仓。
    """
    project_root = root or PROJECT_ROOT
    return list(_dependency_usage_cached(str(project_root.resolve())))


@lru_cache(maxsize=8)
def _dependency_usage_cached(root_str: str) -> tuple[DependencyUsage, ...]:
    """审计结果缓存（按仓库根目录）。

    缓存键是根目录字符串——不同的合成仓库自然落在不同键上。
    """
    return tuple(_audit_dependencies_uncached(Path(root_str)))


def _audit_dependencies_uncached(project_root: Path) -> list[DependencyUsage]:
    """实际执行依赖审计（无缓存）。"""
    pyproject = _read_pyproject(project_root)

    raw_deps: list[str] = list(pyproject.get("project", {}).get("dependencies", []))
    for extra in pyproject.get("project", {}).get("optional-dependencies", {}).values():
        raw_deps.extend(extra)

    runtime_files = _iter_python_files(project_root, *SOURCE_DIRS)
    test_files = _iter_python_files(project_root, TEST_DIR)
    runtime_modules = {path: _module_imports(path) for path in runtime_files}
    test_modules = {path: _module_imports(path) for path in test_files}

    text_files = _iter_repo_text_files(project_root)
    text_blobs = []
    for path in text_files:
        try:
            text_blobs.append(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError):  # pragma: no cover - 二进制/编码异常
            continue

    usages: list[DependencyUsage] = []
    seen: set[str] = set()
    for raw in raw_deps:
        dist = _distribution_name(raw)
        if dist in TOOLCHAIN_DISTRIBUTIONS or dist in seen:
            continue
        seen.add(dist)

        modules = DISTRIBUTION_TO_MODULE.get(dist, (dist.replace("-", "_"),))
        runtime_sites = tuple(
            str(p.relative_to(project_root)).replace("\\", "/")
            for p, mods in runtime_modules.items()
            if mods.intersection(modules)
        )
        test_sites = tuple(
            str(p.relative_to(project_root)).replace("\\", "/")
            for p, mods in test_modules.items()
            if mods.intersection(modules)
        )

        evidence: list[str] = []
        for needle in DIALECT_REFERENCE_PATTERNS.get(dist, ()):
            if any(needle in blob for blob in text_blobs):
                evidence.append(f"方言串引用: {needle}")
        for needle in CLI_ENTRYPOINT_PATTERNS.get(dist, ()):
            if any(needle in blob for blob in text_blobs):
                evidence.append(f"命令行入口: {needle}")

        usages.append(
            DependencyUsage(
                distribution=dist,
                modules=modules,
                runtime_imports=runtime_sites,
                test_imports=test_sites,
                other_evidence=tuple(evidence),
            )
        )
    return usages


def _settings_field_names(settings_src: str) -> list[str]:
    """从 Settings 源码里提取字段名。

    只取「全大写 + 有类型注解」的类体属性——``model_config`` 等非字段属性
    天然被排除（它们不是全大写）。
    """
    return re.findall(r"^\s{4}([A-Z][A-Z0-9_]*)\s*:\s*\S", settings_src, re.M)


def _property_field_map(settings_src: str) -> dict[str, set[str]]:
    """提取 ``@property`` → 其读取的字段集合。

    例如 ``allowed_ips`` 属性读取 ``self.ALLOWED_IPS``，
    于是 ``{"allowed_ips": {"ALLOWED_IPS"}}``。

    **为什么需要这个映射**：本项目大量配置项**只**通过 ``@property``
    被消费（如 ``ALLOWED_IPS`` → ``cfg.allowed_ips``）。
    只按「字段名被直接读取」来判定，会把这些**真实消费方**误报成零消费。
    """
    mapping: dict[str, set[str]] = {}
    try:
        tree = ast.parse(settings_src)
    except SyntaxError:  # pragma: no cover
        return mapping

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "Settings":
            continue
        for item in node.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            decorators = {d.id for d in item.decorator_list if isinstance(d, ast.Name)}
            if "property" not in decorators:
                continue
            fields: set[str] = set()
            for sub in ast.walk(item):
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "self"
                    and sub.attr.isupper()
                ):
                    fields.add(sub.attr)
            mapping[item.name] = fields
    return mapping


def _settings_typed_names(tree: ast.AST) -> set[str]:
    """找出模块/函数作用域内**持有 Settings 实例**的变量名。

    判据：
    - 参数或变量标注里出现 ``Settings``；
    - 赋值来源是 ``get_settings()`` / ``resolve_settings(...)``。

    **必须做这一步**：本代码库大量把 ``Settings`` 别名成 ``cfg``
    （``cfg = settings or get_settings()``），
    只按变量名 ``settings`` 匹配会漏掉一大半真实消费方。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            all_args = [*args.posonlyargs, *args.args, *args.kwonlyargs]
            if args.vararg:
                all_args.append(args.vararg)
            if args.kwarg:
                all_args.append(args.kwarg)
            for arg in all_args:
                if arg.annotation is not None and _mentions_settings(arg.annotation):
                    names.add(arg.arg)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.annotation is not None and _mentions_settings(node.annotation):
                names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            func = value.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in ("get_settings", "resolve_settings"):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _settings_instance_attributes(tree: ast.AST) -> set[str]:
    """找出**实例属性**上持有 Settings 的名字。

    判据：``self.<attr> = <settings 变量>`` 或
    ``self.<attr>: Settings = ...``。

    **必须做这一步**：采集器把 Settings 存在 ``self._settings`` 上，
    之后一律用 ``self._settings.MARKET_FUNDING_POLL_SECONDS`` 读取。
    只匹配「局部变量持有 Settings」会漏掉这一整类消费方，
    而这些字段（轮询间隔、深度档位、重连上限）**恰恰是核心配置**。
    """
    attributes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and node.annotation is not None
                and _mentions_settings(node.annotation)
            ):
                attributes.add(target.attr)
        elif isinstance(node, ast.Assign):
            for assign_target in node.targets:
                if not (
                    isinstance(assign_target, ast.Attribute)
                    and isinstance(assign_target.value, ast.Name)
                    and assign_target.value.id == "self"
                ):
                    continue
                value = node.value
                if isinstance(value, ast.Name):
                    attributes.add(assign_target.attr)
                elif isinstance(value, ast.Call):
                    func = value.func
                    func_name = (
                        func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    )
                    if func_name in ("get_settings", "resolve_settings"):
                        attributes.add(assign_target.attr)
    return attributes


def _mentions_settings(annotation: ast.AST) -> bool:
    """注解里是否提到 ``Settings`` 类型。"""
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id == "Settings":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "Settings":
            return True
        if isinstance(node, ast.Constant) and node.value == "Settings":
            return True
    return False


def audit_settings_fields(root: Path | None = None) -> dict[str, bool]:
    """对账 ``Settings`` 的每个字段与「是否有消费方」。

    返回 ``{字段名: 是否被消费}``。

    **带缓存**：同一进程内对同一仓库的重复调用不重复解析全仓 AST。

    **消费方的定义**：在 ``src/`` / ``scripts/`` / ``migrations/`` 里
    被**真实读取**——

    1. 直接读字段：``cfg.FIELD`` / ``settings.FIELD``；
    2. 通过 ``@property`` 读：``cfg.allowed_ips``（进而读到 ``self.ALLOWED_IPS``）；
    3. 通过**实例属性**读：``self._settings.FIELD``（采集器的统一写法）。

    **不算消费方**：

    - ``settings.py`` **内部**对 ``self.FIELD`` 的引用——
      那只是同一个配置项在自我校验，改配置仍然不会改变任何行为
      （这正是 L-03 / L-19 / L-21 的共性）；
    - ``@field_validator`` 的 ``@classmethod`` 参数引用；
    - **别的类**的同名常量（如 ``ErrorCode.NOTIFY_QUIET_HOURS``）——
      那是另一个枚举的成员，与 Settings 字段无关。
    """
    project_root = root or PROJECT_ROOT
    return dict(_settings_usage_cached(str(project_root.resolve())))


@lru_cache(maxsize=8)
def _settings_usage_cached(root_str: str) -> tuple[tuple[str, bool], ...]:
    """配置项消费情况的缓存（按仓库根目录）。"""
    result = _audit_settings_fields_uncached(Path(root_str))
    return tuple(result.items())


def _audit_settings_fields_uncached(project_root: Path) -> dict[str, bool]:
    """实际执行配置项审计（无缓存）。"""
    settings_path = project_root / "src" / "aicrypto" / "config" / "settings.py"
    settings_src = settings_path.read_text(encoding="utf-8")

    field_names = _settings_field_names(settings_src)
    property_map = _property_field_map(settings_src)

    runtime_files = [
        path for path in _iter_python_files(project_root, *SOURCE_DIRS) if path != settings_path
    ]

    consumed_fields: set[str] = set()
    consumed_properties: set[str] = set()

    for path in runtime_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        typed_names = _settings_typed_names(tree)
        instance_attrs = _settings_instance_attributes(tree)
        if not typed_names and not instance_attrs:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            base = node.value
            if isinstance(base, ast.Name):
                if base.id not in typed_names:
                    continue
            elif (
                isinstance(base, ast.Attribute)
                and isinstance(base.value, ast.Name)
                and base.value.id == "self"
                and base.attr in instance_attrs
            ):
                pass
            else:
                continue
            if node.attr.isupper():
                consumed_fields.add(node.attr)
            elif node.attr in property_map:
                consumed_properties.add(node.attr)

    for prop in consumed_properties:
        consumed_fields.update(property_map.get(prop, set()))

    return {name: name in consumed_fields for name in field_names}


def check_dependency_usage(
    root: Path | None = None,
    whitelist: tuple[WhitelistEntry, ...] = DEFAULT_WHITELIST,
) -> list[str]:
    """检查依赖声明 vs 实际消费，返回不一致项的描述列表。

    空列表表示一致。
    """
    project_root = root or PROJECT_ROOT
    allowed = {entry.key for entry in whitelist}
    problems: list[str] = []

    usages = audit_dependencies(project_root)
    consumed_dists: set[str] = set()

    for usage in usages:
        if usage.is_consumed:
            consumed_dists.add(usage.distribution)
            continue
        if usage.distribution in allowed:
            continue
        detail = ""
        if usage.test_imports:
            detail = f"（仅测试在用：{', '.join(usage.test_imports[:3])}）"
        problems.append(
            f"依赖 {usage.distribution} 在运行时零消费{detail}——"
            f"要么接入消费方，要么从 pyproject.toml 删除，要么在 DEFAULT_WHITELIST 里写明原因"
        )

    # 白名单里的依赖如果**已经有消费方**了，说明白名单该清理了（僵尸债务）。
    for entry in whitelist:
        if entry.key in consumed_dists:
            problems.append(
                f"白名单条目 {entry.key} 已有消费方——请从 DEFAULT_WHITELIST 中删除"
                f"（白名单是「暂缓」清单，不是「永久豁免」清单）"
            )
    return problems


def check_settings_usage(
    root: Path | None = None,
    whitelist: tuple[WhitelistEntry, ...] = DEFAULT_WHITELIST,
) -> list[str]:
    """检查 ``Settings`` 字段 vs 消费方，返回不一致项的描述列表。

    空列表表示一致。
    """
    project_root = root or PROJECT_ROOT
    allowed = {entry.key for entry in whitelist}
    consumed = audit_settings_fields(project_root)
    problems: list[str] = []

    for name, is_consumed in sorted(consumed.items()):
        if is_consumed:
            continue
        if name in allowed:
            continue
        problems.append(
            f"配置项 {name} 没有任何消费方——"
            f"要么接入消费方，要么从 Settings 删除，要么在 DEFAULT_WHITELIST 里写明原因"
        )

    # 白名单里的配置项如果**已经有消费方**了，说明白名单该清理了（僵尸债务）。
    # 与 check_dependency_usage 保持同一口径——否则依赖侧会提醒、配置侧不会，
    # 白名单就会在配置那一半悄悄变成坟场。
    for entry in whitelist:
        if consumed.get(entry.key):
            problems.append(
                f"白名单条目 {entry.key} 已有消费方——请从 DEFAULT_WHITELIST 中删除"
                f"（白名单是「暂缓」清单，不是「永久豁免」清单）"
            )
    return problems


def check_whitelist_reasons(whitelist: tuple[WhitelistEntry, ...] = DEFAULT_WHITELIST) -> list[str]:
    """检查白名单自身是否合规。

    每条必须有非空原因——**没有原因的白名单条目就是永久豁免**，
    那正是本模块要消灭的模式。
    """
    problems: list[str] = []
    seen: set[str] = set()
    for entry in whitelist:
        if not entry.reason.strip():
            problems.append(f"白名单条目 {entry.key} 没有写原因")
        if entry.key in seen:
            problems.append(f"白名单条目 {entry.key} 重复")
        seen.add(entry.key)
    return problems


__all__ = [
    "DEFAULT_WHITELIST",
    "DISTRIBUTION_TO_MODULE",
    "SOURCE_DIRS",
    "TOOLCHAIN_DISTRIBUTIONS",
    "DependencyUsage",
    "WhitelistEntry",
    "audit_dependencies",
    "audit_settings_fields",
    "check_dependency_usage",
    "check_settings_usage",
    "check_whitelist_reasons",
]
