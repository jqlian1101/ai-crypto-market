"""LLM 成本报表的测试（PRD 24.2 / 24.4）。

**背景**：`llm_cost_records` 表早已有真实数据，
但**没有任何东西读它**——又一个「有表无读方」（L-30 那一族）。
没有报表就没有成本基线，而「多 Agent 拆分是否值得」
**只能靠成本报表回答**。

关键不变量：

1. **未定价 ≠ 免费** —— 费用只累加 ``priced=true`` 的行；
   未定价行单独计数，其 token 计入总量但费用不计入。
   直接汇总会把「没登记单价」读成「不花钱」，成本**系统性低估**。
2. **费用是下界时必须显式声明** —— 报表要标出存在未定价调用。
3. **分组维度覆盖 PRD 24.4 的要求** —— 模型 / 档位 / Agent /
   Prompt 版本，外加状态与按日。
4. **金额不用浮点** —— 全程 ``Decimal``，渲染与 JSON 都不经过 float。
5. **中文表格对齐** —— 按**显示宽度**（CJK 占 2 列）补齐。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from aicrypto.services.cost_report import (
    CostRow,
    build_cost_report,
    render_cost_report,
    snapshot_is_priced,
)

#: 固定的基准时刻（测试不依赖真实时钟）
BASE = dt.datetime(2026, 11, 27, 12, 0, 0, tzinfo=dt.UTC)


def _row(
    *,
    model_id: str = "model-a",
    agent_name: str | None = "master",
    tier: str | None = "TIER_STANDARD",
    prompt_version: str | None = "v1",
    status: str | None = "SUCCESS",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cost_usd: str = "0.0105",
    priced: bool = True,
    day: int = 27,
) -> CostRow:
    """构造一条成本行（``priced=False`` 时费用会被忽略）。"""
    return CostRow(
        created_at=BASE.replace(day=day),
        model_id=model_id,
        agent_name=agent_name,
        tier=tier,
        prompt_version=prompt_version,
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=Decimal(cost_usd),
        priced=priced,
    )


# --------------------------------------------------------------------------
# 快照定价标记
# --------------------------------------------------------------------------


def test_snapshot_priced_true() -> None:
    assert snapshot_is_priced({"priced": True}) is True


def test_snapshot_priced_false() -> None:
    assert snapshot_is_priced({"priced": False}) is False


def test_snapshot_missing_priced_key_is_unpriced() -> None:
    """**缺失即视为未定价**。

    保守方向：宁可少报费用，也不把「没标记」误读成「免费」。
    """
    assert snapshot_is_priced({"model_id": "x", "currency": "USD"}) is False


def test_snapshot_not_a_dict_is_unpriced() -> None:
    """快照不是 dict（脏数据）时按未定价处理，不得抛异常。"""
    assert snapshot_is_priced(None) is False
    assert snapshot_is_priced("garbage") is False
    assert snapshot_is_priced([1, 2]) is False


def test_snapshot_priced_string_true() -> None:
    """历史快照里 ``"true"`` 字符串也应被识别。"""
    assert snapshot_is_priced({"priced": "true"}) is True
    assert snapshot_is_priced({"priced": "TRUE"}) is True
    assert snapshot_is_priced({"priced": "false"}) is False


# --------------------------------------------------------------------------
# 核心纪律：未定价 ≠ 免费
# --------------------------------------------------------------------------


def test_unpriced_row_does_not_contribute_cost() -> None:
    """未定价行的费用**不得**进总额。

    这是整个模块存在的理由：
    ``estimated_cost_usd`` 是 NOT NULL，单价缺失时只能写 0，
    直接 ``SUM`` 会把「没登记单价」读成「不花钱」。
    """
    rows = [
        _row(cost_usd="0.0105", priced=True),
        _row(cost_usd="0.9999", priced=False),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    assert report.total.cost_usd == Decimal("0.0105")


def test_unpriced_row_tokens_still_counted() -> None:
    """未定价行的 **token 仍计入总量**——那是真实消耗的资源。

    只有「费用」被隔离，token 没有理由被隔离：
    资源消耗是事实，单价缺失只影响「花了多少钱」的答案。
    """
    rows = [
        _row(input_tokens=1000, output_tokens=500, priced=True),
        _row(input_tokens=2000, output_tokens=800, priced=False),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    assert report.total.input_tokens == 3000
    assert report.total.output_tokens == 1300
    assert report.total.total_tokens == 4300
    assert report.total.unpriced_calls == 1
    assert report.total.unpriced_input_tokens == 2000
    assert report.total.unpriced_output_tokens == 800


def test_report_flags_unpriced_as_lower_bound() -> None:
    """存在未定价调用时，报表必须声明费用是**下界**。"""
    rows = [_row(priced=False)]
    report = build_cost_report(rows, since=BASE, until=BASE)
    assert report.has_unpriced is True
    assert report.is_lower_bound is True


def test_report_without_unpriced_is_not_lower_bound() -> None:
    rows = [_row(priced=True)]
    report = build_cost_report(rows, since=BASE, until=BASE)
    assert report.has_unpriced is False
    assert report.is_lower_bound is False


def test_unpriced_models_are_listed_separately() -> None:
    """未定价模型要**单独列出**——运维才能据此去补 ``model_pricing``。"""
    rows = [
        _row(model_id="model-a", priced=True),
        _row(model_id="model-b", priced=False, input_tokens=100, output_tokens=50),
        _row(model_id="model-c", priced=False, input_tokens=900, output_tokens=450),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    unpriced = {m.model_id: m for m in report.unpriced_models}
    assert set(unpriced) == {"model-b", "model-c"}
    assert unpriced["model-b"].calls == 1
    assert unpriced["model-c"].total_tokens == 1350


def test_unpriced_models_sorted_by_tokens_desc() -> None:
    """未定价模型按 token 降序——**最该补单价的排最前**。

    全部未定价时费用都是 0，只按费用排会让顺序失去意义。
    """
    rows = [
        _row(model_id="small", priced=False, input_tokens=10, output_tokens=5),
        _row(model_id="big", priced=False, input_tokens=9000, output_tokens=5000),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    assert [m.model_id for m in report.unpriced_models] == ["big", "small"]


def test_unpriced_calls_counted_per_bucket() -> None:
    """每个分组里也要单独记未定价调用数。"""
    rows = [
        _row(model_id="model-a", priced=True),
        _row(model_id="model-a", priced=False),
        _row(model_id="model-b", priced=False),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    by_model = {b.key: b for b in report.by_model}
    assert by_model["model-a"].calls == 2
    assert by_model["model-a"].unpriced_calls == 1
    assert by_model["model-b"].unpriced_calls == 1


# --------------------------------------------------------------------------
# 分组维度
# --------------------------------------------------------------------------


def test_group_by_model() -> None:
    rows = [
        _row(model_id="model-a", cost_usd="1.0"),
        _row(model_id="model-a", cost_usd="2.0"),
        _row(model_id="model-b", cost_usd="0.5"),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    by_model = {b.key: b for b in report.by_model}
    assert by_model["model-a"].calls == 2
    assert by_model["model-a"].cost_usd == Decimal("3.0")
    assert by_model["model-b"].cost_usd == Decimal("0.5")


def test_group_by_tier() -> None:
    """``model_runs`` 没有 ``tier`` 列，档位只在成本行上（PRD 30.4 注）。"""
    rows = [
        _row(tier="TIER_LIGHT", cost_usd="1.0"),
        _row(tier="TIER_DEEP", cost_usd="2.0"),
        _row(tier="TIER_DEEP", cost_usd="3.0"),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    by_tier = {b.key: b for b in report.by_tier}
    assert by_tier["TIER_DEEP"].calls == 2
    assert by_tier["TIER_DEEP"].cost_usd == Decimal("5.0")
    assert by_tier["TIER_LIGHT"].calls == 1


def test_group_by_agent() -> None:
    """多 Agent 拆分的成本归因维度。"""
    rows = [
        _row(agent_name="technical", cost_usd="1.0"),
        _row(agent_name="market-structure", cost_usd="2.0"),
        _row(agent_name="master", cost_usd="4.0"),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    by_agent = {b.key: b for b in report.by_agent}
    assert by_agent["master"].cost_usd == Decimal("4.0")
    assert by_agent["technical"].cost_usd == Decimal("1.0")


def test_group_by_prompt_version() -> None:
    """「哪版 Prompt 最烧 token」——报表必须能回答。"""
    rows = [
        _row(prompt_version="v1", input_tokens=1000, output_tokens=500),
        _row(prompt_version="v2", input_tokens=3000, output_tokens=1500),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    by_prompt = {b.key: b for b in report.by_prompt_version}
    assert by_prompt["v1"].total_tokens == 1500
    assert by_prompt["v2"].total_tokens == 4500


def test_group_by_status() -> None:
    """失败调用烧的钱也是钱——按状态分组让「失败成本」可见。"""
    rows = [
        _row(status="SUCCESS", cost_usd="1.0"),
        _row(status="FAILED", cost_usd="2.0"),
        _row(status="TIMEOUT", cost_usd="3.0"),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    by_status = {b.key: b for b in report.by_status}
    assert by_status["FAILED"].cost_usd == Decimal("2.0")
    assert by_status["TIMEOUT"].cost_usd == Decimal("3.0")


def test_group_by_day() -> None:
    """按日分组，且按**日期升序**（时间序，不是费用序）。"""
    rows = [
        _row(day=25, cost_usd="3.0"),
        _row(day=27, cost_usd="1.0"),
        _row(day=26, cost_usd="2.0"),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    assert [b.key for b in report.by_day] == ["2026-11-25", "2026-11-26", "2026-11-27"]


def test_null_dimension_becomes_unknown() -> None:
    """维度为 NULL 时归入「(未知)」，**不得**悄悄消失。

    丢失一行会让费用凭空减少——报表**少算**比**多算**更危险，
    因为读者看不出少了东西。
    """
    rows = [
        _row(agent_name=None, tier=None, prompt_version=None, status=None),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    assert report.by_agent[0].key == "(未知)"
    assert report.by_tier[0].key == "(未知)"
    assert report.by_prompt_version[0].key == "(未知)"
    assert report.by_status[0].key == "(未知)"
    assert report.total.calls == 1


def test_buckets_sorted_by_cost_desc() -> None:
    """分组按费用降序——「最贵的排最前」。"""
    rows = [
        _row(model_id="cheap", cost_usd="1.0"),
        _row(model_id="expensive", cost_usd="9.0"),
        _row(model_id="middle", cost_usd="5.0"),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    assert [b.key for b in report.by_model] == ["expensive", "middle", "cheap"]


def test_empty_rows_produce_empty_report() -> None:
    """没有调用时也要能出报表（不能抛异常）。"""
    report = build_cost_report([], since=BASE, until=BASE)
    assert report.total.calls == 0
    assert report.total.cost_usd == Decimal(0)
    assert report.by_model == []
    assert report.has_unpriced is False


def test_total_equals_sum_of_priced_rows() -> None:
    """总额必须等于**所有已定价行**之和（跨分组一致）。"""
    rows = [
        _row(model_id="a", tier="TIER_LIGHT", cost_usd="1.5"),
        _row(model_id="b", tier="TIER_DEEP", cost_usd="2.5"),
        _row(model_id="c", tier="TIER_DEEP", cost_usd="4.0", priced=False),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    assert report.total.cost_usd == Decimal("4.0")
    assert sum(b.cost_usd for b in report.by_model) == Decimal("4.0")
    assert sum(b.cost_usd for b in report.by_tier) == Decimal("4.0")


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------


def test_render_contains_summary() -> None:
    """渲染结果要含关键读数：总调用、总 token、总费用。"""
    rows = [_row(input_tokens=1500, output_tokens=750, cost_usd="0.0250")]
    report = build_cost_report(rows, since=BASE, until=BASE)
    text = render_cost_report(report)
    assert "1 次" in text
    assert "2,250" in text
    assert "$0.0250" in text


def test_render_warns_on_unpriced() -> None:
    """存在未定价调用时，渲染结果必须**显眼地**警告。"""
    rows = [_row(priced=False)]
    report = build_cost_report(rows, since=BASE, until=BASE)
    text = render_cost_report(report)
    assert "未定价" in text
    assert "下界" in text


def test_render_no_warning_when_all_priced() -> None:
    rows = [_row(priced=True)]
    report = build_cost_report(rows, since=BASE, until=BASE)
    text = render_cost_report(report)
    assert "下界" not in text


def test_render_lists_unpriced_models() -> None:
    """未定价模型名要出现在**警告段**里（运维据此补单价）。

    **判据必须限定在警告段**：模型名同时也会出现在「按模型」分组表里，
    若只断言「全文包含该模型名」，那么即使警告段完全没提它，
    断言也会成立——**vacuous pass**。
    因此这里只取警告段（``!! 警告`` 到下一个空行为止）来断言。
    """
    rows = [_row(model_id="mystery-model", priced=False)]
    report = build_cost_report(rows, since=BASE, until=BASE)
    text = render_cost_report(report)

    warning_section = _extract_warning_section(text)
    assert warning_section, "报表里找不到警告段"
    assert "mystery-model" in warning_section, f"警告段没有列出未定价模型：\n{warning_section}"


def _extract_warning_section(text: str) -> str:
    """截取「!! 警告」段落（到下一个空行为止）。"""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if "!! 警告" in line)
    except StopIteration:
        return ""
    collected: list[str] = []
    for line in lines[start:]:
        if line.strip() == "":
            break
        collected.append(line)
    return "\n".join(collected)


def test_render_empty_report_does_not_crash() -> None:
    report = build_cost_report([], since=BASE, until=BASE)
    text = render_cost_report(report)
    assert "没有任何调用记录" in text


def test_render_table_aligns_cjk() -> None:
    """中文表头下表格必须对齐。

    用 ``len()`` 对齐中文会错位——报表全是中文表头，
    宽度算错会直接毁掉可读性。这里断言**每行显示宽度一致**。
    """
    from aicrypto.services.cost_report import _display_width

    rows = [
        _row(model_id="model-a", agent_name="technical"),
        _row(model_id="模型乙", agent_name="市场结构"),
    ]
    report = build_cost_report(rows, since=BASE, until=BASE)
    text = render_cost_report(report)

    table_lines = [
        line for line in text.splitlines() if line.startswith("model-") or "模型乙" in line
    ]
    assert table_lines, "报表里应当出现模型分组行"
    widths = {_display_width(line) for line in table_lines}
    # 同一张表里的数据行显示宽度应一致（末列右对齐后等宽）
    assert len(widths) == 1, f"表格行显示宽度不一致：{widths}"


def test_render_money_has_no_float_error() -> None:
    """金额渲染不得出现浮点误差尾巴。"""
    rows = [_row(cost_usd="0.1")]
    report = build_cost_report(rows, since=BASE, until=BASE)
    text = render_cost_report(report)
    assert "$0.1000" in text
    assert "0.0999" not in text
