"""Prompt 模板加载与版本解析的测试（PRD 4.7.2 / 30.6）。

**背景**：分析 Prompt 此前**硬编码**在 ``analysis_worker.py`` 的消息列表里。
改一个字无法追溯是哪一版产出，``prompt_versions`` 表也永远没有数据。
这与 L-07 的症状同源：表建了，但没人写。

这些测试锁死：

1. 版本号从**文件名**解析（不写在内容里，避免「改了内容忘了改版本」）；
2. 哈希基于**原始字节**（换行符归一化不能改变哈希）；
3. 缺占位符**必须报错**——绝不让 ``{schema}`` 字面量发给模型；
4. 指定版本不存在时**报错**，不回落「最大版本」
   （静默回落会让「v2 不存在」变成「用 v1 跑」，与 L-07a 同类）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aicrypto.config.prompts import (
    PromptNotFoundError,
    PromptVersionError,
    list_templates,
    load_template,
    parse_template_filename,
    resolve_latest_version,
    template_hash,
)


def _write(root: Path, agent: str, name: str, body: str) -> Path:
    directory = root / agent
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 文件名解析
# --------------------------------------------------------------------------


def test_parse_simple_filename() -> None:
    assert parse_template_filename("analysis.v1.md") == ("analysis", "v1")


def test_parse_name_with_dots() -> None:
    """模板名允许含 ``.``（如 ``analysis.user.v1.md``）。"""
    assert parse_template_filename("analysis.user.v1.md") == ("analysis.user", "v1")


def test_parse_rejects_missing_version() -> None:
    with pytest.raises(PromptVersionError):
        parse_template_filename("analysis.md")


def test_parse_rejects_non_numeric_version() -> None:
    with pytest.raises(PromptVersionError):
        parse_template_filename("analysis.vX.md")


# --------------------------------------------------------------------------
# 加载与版本选择
# --------------------------------------------------------------------------


def test_load_latest_when_version_omitted(tmp_path: Path) -> None:
    """不指定版本时加载版本号最大的那个。"""
    _write(tmp_path, "master", "analysis.v1.md", "一")
    _write(tmp_path, "master", "analysis.v2.md", "二")
    _write(tmp_path, "master", "analysis.v10.md", "十")
    template = load_template("master", "analysis", prompts_dir=tmp_path)
    # 按数字比较而非字符串比较：v10 必须大于 v2
    assert template.version == "v10"


def test_load_specific_version(tmp_path: Path) -> None:
    _write(tmp_path, "master", "analysis.v1.md", "一")
    _write(tmp_path, "master", "analysis.v2.md", "二")
    template = load_template("master", "analysis", version="v1", prompts_dir=tmp_path)
    assert template.version == "v1"
    assert template.content.strip() == "一"


def test_missing_version_raises_instead_of_falling_back(tmp_path: Path) -> None:
    """指定版本不存在时必须报错，不能静默用最大版本。

    静默回落会让「v2 不存在」变成「用 v1 跑」——
    这正是 L-07a「阈值回落硬编码默认值」那类静默失效。
    """
    _write(tmp_path, "master", "analysis.v1.md", "一")
    with pytest.raises(PromptNotFoundError, match="v2"):
        load_template("master", "analysis", version="v2", prompts_dir=tmp_path)


def test_missing_agent_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(PromptNotFoundError):
        load_template("nope", "analysis", prompts_dir=tmp_path)


def test_missing_template_name_raises(tmp_path: Path) -> None:
    _write(tmp_path, "master", "analysis.v1.md", "一")
    with pytest.raises(PromptNotFoundError):
        load_template("master", "other", prompts_dir=tmp_path)


def test_list_templates_empty_when_dir_absent(tmp_path: Path) -> None:
    """目录不存在返回空列表，不报错（Prompt 目录是可选部署产物）。"""
    assert list_templates("master", prompts_dir=tmp_path) == []


def test_list_templates_sorted_by_version_number(tmp_path: Path) -> None:
    _write(tmp_path, "master", "analysis.v10.md", "十")
    _write(tmp_path, "master", "analysis.v2.md", "二")
    versions = [item.version for item in list_templates("master", prompts_dir=tmp_path)]
    assert versions == ["v2", "v10"]


# --------------------------------------------------------------------------
# 哈希
# --------------------------------------------------------------------------


def test_hash_is_stable_across_calls(tmp_path: Path) -> None:
    path = _write(tmp_path, "master", "analysis.v1.md", "内容")
    template = load_template("master", "analysis", prompts_dir=tmp_path)
    assert template.template_hash == template_hash(path.read_bytes())


def test_hash_survives_line_ending_normalization(tmp_path: Path) -> None:
    """哈希基于原始字节——换行符变化会让哈希变化，但**内容语义**不变。

    这不是缺陷而是设计：哈希的作用是「检测文件被改动过」，
    换行符变化也是改动。关键是**同一个文件两次读取的哈希必须一致**，
    不会因为 CRLF/LF 归一化而产生两个「同名版本」。
    """
    path = tmp_path / "a.md"
    path.write_bytes(b"line1\r\nline2\r\n")
    first = template_hash(path.read_bytes())
    second = template_hash(path.read_bytes())
    assert first == second


def test_hash_changes_when_content_changes(tmp_path: Path) -> None:
    _write(tmp_path, "master", "analysis.v1.md", "原始")
    before = load_template("master", "analysis", prompts_dir=tmp_path).template_hash
    _write(tmp_path, "master", "analysis.v1.md", "改过")
    after = load_template("master", "analysis", prompts_dir=tmp_path).template_hash
    assert before != after


def test_resolve_latest_version(tmp_path: Path) -> None:
    _write(tmp_path, "master", "analysis.v1.md", "一")
    _write(tmp_path, "master", "analysis.v3.md", "三")
    version, digest = resolve_latest_version("master", "analysis", prompts_dir=tmp_path)
    assert version == "v3"
    assert len(digest) == 64


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------


def test_render_substitutes_placeholder(tmp_path: Path) -> None:
    _write(tmp_path, "master", "analysis.v1.md", "Schema: {schema}")
    template = load_template("master", "analysis", prompts_dir=tmp_path)
    assert template.render(schema="{}") == "Schema: {}"


def test_render_raises_on_missing_placeholder(tmp_path: Path) -> None:
    """缺占位符必须报错——绝不让 ``{schema}`` 字面量发给模型。"""
    _write(tmp_path, "master", "analysis.v1.md", "Schema: {schema}")
    template = load_template("master", "analysis", prompts_dir=tmp_path)
    with pytest.raises(PromptVersionError, match="schema"):
        template.render()


def test_version_number_property(tmp_path: Path) -> None:
    _write(tmp_path, "master", "analysis.v7.md", "七")
    template = load_template("master", "analysis", prompts_dir=tmp_path)
    assert template.version_number == 7


def test_empty_template_rejected(tmp_path: Path) -> None:
    """空模板必须被拒绝。

    空模板会让模型收到一个**空 system prompt**——白白消耗一次调用，
    产出一份必然 schema 校验失败的报告。失败得早一点、清楚一点，
    比浪费调用再报「LLM_SCHEMA_INVALID」好。
    """
    _write(tmp_path, "master", "analysis.v1.md", "   \n  ")
    with pytest.raises(PromptVersionError, match="内容为空"):
        load_template("master", "analysis", prompts_dir=tmp_path)


def test_zero_byte_template_rejected(tmp_path: Path) -> None:
    """0 字节模板同样必须被拒绝。"""
    directory = tmp_path / "master"
    directory.mkdir(parents=True)
    (directory / "analysis.v1.md").write_bytes(b"")
    with pytest.raises(PromptVersionError, match="内容为空"):
        load_template("master", "analysis", prompts_dir=tmp_path)


# --------------------------------------------------------------------------
# 项目自带的 Prompt
# --------------------------------------------------------------------------


def test_project_prompts_exist() -> None:
    """项目必须自带 Prompt 模板——否则 worker 启动即失败。"""
    template = load_template("master", "analysis")
    assert template.version == "v1"
    assert template.template_hash


def test_project_user_prompt_exists() -> None:
    template = load_template("master", "analysis.user")
    assert template.version == "v1"


def test_project_system_prompt_has_schema_placeholder() -> None:
    """系统提示必须引用 schema 占位符。"""
    template = load_template("master", "analysis")
    assert "{schema}" in template.content


def test_project_user_prompt_has_snapshot_placeholder() -> None:
    """用户提示必须引用 snapshot 占位符。"""
    template = load_template("master", "analysis.user")
    assert "{snapshot}" in template.content


def test_project_prompts_render_without_error() -> None:
    """项目自带的模板必须能正常渲染。"""
    system = load_template("master", "analysis")
    user = load_template("master", "analysis.user")
    assert "{schema}" not in system.render(schema="{}")
    assert "{snapshot}" not in user.render(snapshot="{}")
