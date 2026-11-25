"""Prompt 模板的发现、加载与版本解析（PRD 4.7.2 / 30.6 / 32.4）。

**为什么需要这一层**：分析 Prompt 此前**硬编码**在
``services/analysis_worker.py`` 的 ``messages`` 列表里。改一个字的 prompt
无法追溯「这条报告是哪一版产出」，``prompt_versions`` 表也永远没有数据。
这与 L-07 的症状同源：表建了，但没人写。

本模块建立完整链路::

    prompts/<agent>/<name>.v<n>.md  ->  PromptTemplate  ->  prompt_versions 表

**版本号从文件名解析**：文件名形如 ``analysis.v1.md``，版本号为 ``v1``。
版本号**不写在文件内容里**——写在内容里会产生「改了内容但忘了改版本号」
的漂移，而文件名在版本控制里，改内容时 diff 一眼可见。

**哈希用原始字节**：``template_hash`` 记录文件内容的 SHA-256，
用于校验「落库的版本」与「磁盘上的文件」是同一份。哈希基于**原始字节**
而非解析后的字符串，避免换行符归一化（Windows CRLF vs Unix LF）导致
同一份内容算出两个哈希——那会让「同一版本」在审计时看起来像两个版本。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from aicrypto.config.settings import PROJECT_ROOT

#: Prompt 模板根目录
PROMPTS_DIR = PROJECT_ROOT / "prompts"

#: 版本号格式：``v`` + 数字
VERSION_PATTERN = re.compile(r"^v(?P<number>\d+)$")

#: 模板文件名格式：``<name>.v<n>.md``（name 允许含 ``.``，取最后一个 ``.v<n>`` 前的部分）
TEMPLATE_FILENAME_PATTERN = re.compile(r"^(?P<name>.+)\.(?P<version>v\d+)\.md$")


#: 缺少模板时的错误码（与 ``common/errors.py`` 的字典保持同一命名风格）
class PromptNotFoundError(FileNotFoundError):
    """Prompt 模板不存在。"""


class PromptVersionError(ValueError):
    """Prompt 版本号非法。"""


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """一个已加载的 Prompt 模板。"""

    agent_name: str
    template_name: str
    version: str
    path: Path
    content: str
    template_hash: str

    @property
    def version_number(self) -> int:
        """版本号数字部分，用于比较新旧。"""
        match = VERSION_PATTERN.match(self.version)
        if match is None:
            raise PromptVersionError(f"非法 Prompt 版本号: {self.version!r}")
        return int(match.group("number"))

    def render(self, **kwargs: str) -> str:
        """渲染模板。

        **不做静默回落**：模板里的占位符若没有对应参数，会保留 ``{xxx}``
        原样发给模型——这比报错更糟，因为模型会把 ``{schema}`` 当成字面量。
        因此未知占位符在这里直接报错。
        """
        try:
            return self.content.format(**kwargs)
        except KeyError as exc:
            raise PromptVersionError(
                f"Prompt 模板 {self.path} 缺少占位符 {exc.args[0]!r} 的渲染值"
            ) from exc


def template_hash(content: bytes) -> str:
    """计算模板内容的 SHA-256（十六进制）。

    对**原始字节**求值：保持与落库时的哈希一致。
    """
    return hashlib.sha256(content).hexdigest()


def parse_template_filename(filename: str) -> tuple[str, str]:
    """从文件名解析 ``(template_name, version)``。

    ``analysis.v1.md`` -> ``("analysis", "v1")``。

    非法格式抛 :class:`PromptVersionError`，不静默返回默认值——
    静默回落会让「模板名写错」变成「找不到模板」这种难排查的间接错误。
    """
    match = TEMPLATE_FILENAME_PATTERN.match(filename)
    if match is None:
        raise PromptVersionError(f"Prompt 模板文件名 {filename!r} 不符合 <name>.v<n>.md 格式")
    version = match.group("version")
    if VERSION_PATTERN.match(version) is None:
        raise PromptVersionError(f"Prompt 版本号 {version!r} 非法")
    return match.group("name"), version


def list_templates(agent_name: str, prompts_dir: Path | None = None) -> list[PromptTemplate]:
    """列出某个 Agent 的全部模板，按版本号升序。

    目录不存在时返回空列表——不报错。Prompt 目录是可选的部署产物，
    缺失时应由调用方决定是降级还是失败。
    """
    root = prompts_dir or PROMPTS_DIR
    agent_dir = root / agent_name
    if not agent_dir.is_dir():
        return []
    templates: list[PromptTemplate] = []
    for path in sorted(agent_dir.iterdir()):
        if not path.is_file() or path.suffix != ".md":
            continue
        name, version = parse_template_filename(path.name)
        raw = path.read_bytes()
        if not raw.strip():
            # 空模板必须拒绝：它会让模型收到一个空 system prompt，
            # 白白消耗一次调用，产出一份必然 schema 校验失败的报告。
            # 失败得早一点、清楚一点，比浪费调用再报「LLM_SCHEMA_INVALID」好。
            raise PromptVersionError(f"Prompt 模板 {path} 内容为空")
        templates.append(
            PromptTemplate(
                agent_name=agent_name,
                template_name=name,
                version=version,
                path=path,
                content=raw.decode("utf-8"),
                template_hash=template_hash(raw),
            )
        )
    templates.sort(key=lambda item: item.version_number)
    return templates


def load_template(
    agent_name: str,
    template_name: str,
    *,
    version: str | None = None,
    prompts_dir: Path | None = None,
) -> PromptTemplate:
    """加载指定 Agent 的 Prompt 模板。

    ``version`` 缺省时加载**版本号最大**的模板。
    指定了不存在的版本时抛 :class:`PromptNotFoundError`——
    不回落到「最大版本」，因为那会让「v2 不存在」静默变成「用 v1 跑」，
    与 L-07a 的「阈值回落硬编码默认值」是同一类静默失效。
    """
    templates = list_templates(agent_name, prompts_dir=prompts_dir)
    if not templates:
        raise PromptNotFoundError(f"Agent {agent_name!r} 下没有找到任何 Prompt 模板")
    matching = [item for item in templates if item.template_name == template_name]
    if not matching:
        available = sorted({item.template_name for item in templates})
        raise PromptNotFoundError(
            f"Agent {agent_name!r} 下没有名为 {template_name!r} 的模板；可用：{available}"
        )
    if version is None:
        return matching[-1]
    for item in matching:
        if item.version == version:
            return item
    available = [item.version for item in matching]
    raise PromptNotFoundError(
        f"Prompt {agent_name}/{template_name} 没有版本 {version!r}；可用：{available}"
    )


def resolve_latest_version(
    agent_name: str,
    template_name: str,
    *,
    prompts_dir: Path | None = None,
) -> tuple[str, str]:
    """解析最新版本号与内容哈希，供写入 ``prompt_versions`` 表。

    返回 ``(version, template_hash)``。
    """
    template = load_template(agent_name, template_name, prompts_dir=prompts_dir)
    return template.version, template.template_hash


__all__ = [
    "PROMPTS_DIR",
    "TEMPLATE_FILENAME_PATTERN",
    "VERSION_PATTERN",
    "PromptNotFoundError",
    "PromptTemplate",
    "PromptVersionError",
    "list_templates",
    "load_template",
    "parse_template_filename",
    "resolve_latest_version",
    "template_hash",
]
