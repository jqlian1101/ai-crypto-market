"""Master 输出 → 报告内容的转换层测试（PRD 4.7.1 / 4.7.2 / 32.4）。

**为什么单独一个文件**：转换层是「Agent 视角」与「用户视角」之间的桥。
桥的两侧纪律都极易被悄悄破坏：

1. **事实/推断/假设的标注必须原样保留**——
   Master 的 ``findings`` 用 ``【事实】`` / ``【推断】`` / ``【假设】`` 前缀，
   这是它履行「区分事实、推断和待验证假设」这条纪律的**唯一证据**。
   转换时一旦把标注洗掉，报告看起来还是「有结论」，
   但读者**无法分辨哪句是事实、哪句是猜测**——
   纪律就等于没执行，而且**没有任何人会发现**。

2. **没把握时必须用 ``UNAVAILABLE``，绝不能用 ``NEUTRAL``**——
   ``NEUTRAL`` 是「我看下来是震荡」（一个有方向的判断），
   ``UNAVAILABLE`` 是「我没结论」。
   把两者混同，会让「没分析出来」在报表上看起来像「分析出来是震荡」
   （PRD 24.4.1 部分结果红线）。

3. **不得虚构证据引用**（PRD 4.7.1）——
   Master 的 ``supporting_evidence`` 是自然语言描述，
   **不是** ``EvidenceRef``。硬塞会造出无法定位的引用。
"""

from __future__ import annotations

from aicrypto.domain.analysis.agent_output import AgentOutput
from aicrypto.services.multi_agent_report import (
    BIAS_NEUTRAL,
    BIAS_UNAVAILABLE,
    master_to_report_content,
)

#: **标注的字面值**——**故意不从被测模块 import**。
#:
#: 如果从模块 import ``LITERAL_FACT_MARK``，那么「把 ``LITERAL_FACT_MARK`` 改成空串」
#: 这个破坏会让**输入与断言同时变空**——
#: 断言「包含空串」永远成立，用例就**锁不住任何东西**。
#:
#: 这正是本项目在反向验证里反复抓到的
#: 「**有断言 ≠ 断言有效**」——
#: 上一次就发生在成本报表的「模型名断言」上。
#: 这里的做法是：**断言里用字面值**，让破坏真实地体现在输出上。
LITERAL_FACT_MARK = "【事实】"
LITERAL_INFERENCE_MARK = "【推断】"
LITERAL_HYPOTHESIS_MARK = "【假设】"


def _output(**overrides: object) -> AgentOutput:
    base: dict[str, object] = {
        "agent_name": "master",
        "agent_version": "v1",
        "model_id": "test-model",
        "prompt_version": "v1",
        "market_snapshot_id": "snap_test",
        "as_of": "2026-11-27T00:00:00Z",
        "time_horizon": "4h",
        "summary": "整体偏多，但存在结构性分歧。",
        "findings": [],
        "confidence": 0.6,
    }
    base.update(overrides)
    return AgentOutput.model_validate(base)


# --------------------------------------------------------------------------
# 事实 / 推断 / 假设的标注
# --------------------------------------------------------------------------


def test_fact_marker_is_preserved_in_dominant_factors() -> None:
    """【事实】标注必须原样出现在报告的 ``dominant_factors`` 里。"""
    content = master_to_report_content(
        _output(findings=[f"{LITERAL_FACT_MARK}价格站上 200 日均线"])
    )
    assert any(LITERAL_FACT_MARK in item for item in content.dominant_factors)
    assert any("200 日均线" in item for item in content.dominant_factors)


def test_inference_marker_is_preserved_in_dominant_factors() -> None:
    """【推断】标注必须原样出现在报告的 ``dominant_factors`` 里。"""
    content = master_to_report_content(
        _output(findings=[f"{LITERAL_INFERENCE_MARK}资金费率转正说明多头情绪回暖"])
    )
    assert any(LITERAL_INFERENCE_MARK in item for item in content.dominant_factors)


def test_hypothesis_marker_is_preserved_in_disagreements() -> None:
    """【假设】标注必须原样出现在报告的 ``disagreements`` 里。

    假设是**未验证**的推测——把它混进「主导因素」会把它抬高成结论，
    混进「不确定性」才是它该在的地方。
    """
    content = master_to_report_content(
        _output(findings=[f"{LITERAL_HYPOTHESIS_MARK}若成交量持续放大则可能启动突破"])
    )
    assert any(LITERAL_HYPOTHESIS_MARK in item for item in content.disagreements)


def test_markers_are_not_merged_together() -> None:
    """三类标注必须**各自可辨**——不能被压成同一种。"""
    content = master_to_report_content(
        _output(
            findings=[
                f"{LITERAL_FACT_MARK}这是事实",
                f"{LITERAL_INFERENCE_MARK}这是推断",
                f"{LITERAL_HYPOTHESIS_MARK}这是假设",
            ]
        )
    )
    all_text = " ".join(content.dominant_factors + content.disagreements)
    assert LITERAL_FACT_MARK in all_text
    assert LITERAL_INFERENCE_MARK in all_text
    assert LITERAL_HYPOTHESIS_MARK in all_text


def test_unmarked_findings_are_preserved_not_dropped() -> None:
    """没标注的发现**不能被丢掉**。

    强行分类等于替 Master 做了它没做的判断；
    但完全丢弃等于**吞掉信息**——读者会以为 Master 没说过这话。
    正确做法是**保留原样**。
    """
    content = master_to_report_content(_output(findings=["没标注的发现"]))
    all_text = " ".join(content.dominant_factors + content.disagreements)
    assert "没标注的发现" in all_text


def test_facts_and_inferences_go_to_dominant_factors_not_disagreements() -> None:
    """事实与推断进 ``dominant_factors``——它们不是分歧。"""
    content = master_to_report_content(
        _output(
            findings=[
                f"{LITERAL_FACT_MARK}这是事实",
                f"{LITERAL_INFERENCE_MARK}这是推断",
            ]
        )
    )
    joined_factors = " ".join(content.dominant_factors)
    joined_disagreements = " ".join(content.disagreements)
    assert "这是事实" in joined_factors
    assert "这是推断" in joined_factors
    assert "这是事实" not in joined_disagreements
    assert "这是推断" not in joined_disagreements


# --------------------------------------------------------------------------
# market_bias 的取值纪律
# --------------------------------------------------------------------------


def test_bullish_summary_maps_to_bullish_bias() -> None:
    content = master_to_report_content(_output(summary="整体看涨。"))
    assert content.market_bias == "BULLISH"


def test_bearish_summary_maps_to_bearish_bias() -> None:
    content = master_to_report_content(_output(summary="整体看跌。"))
    assert content.market_bias == "BEARISH"


def test_flat_summary_maps_to_neutral_bias() -> None:
    content = master_to_report_content(_output(summary="市场震荡。"))
    assert content.market_bias == BIAS_NEUTRAL


def test_unclear_summary_maps_to_unavailable_not_neutral() -> None:
    """识别不出方向时必须 ``UNAVAILABLE``，**绝不能** ``NEUTRAL``。

    ``NEUTRAL`` 是「我看下来是震荡」；
    ``UNAVAILABLE`` 是「我没结论」。
    混同会让「没分析出来」看起来像「分析出来是震荡」。
    """
    content = master_to_report_content(_output(summary="数据不完整，无法判断。"))
    assert content.market_bias == BIAS_UNAVAILABLE
    assert content.market_bias != BIAS_NEUTRAL


def test_ambiguous_summary_maps_to_unavailable() -> None:
    """同时含涨跌两种措辞时**不能瞎猜**——必须 ``UNAVAILABLE``。"""
    content = master_to_report_content(_output(summary="短期看涨，中期看跌。"))
    assert content.market_bias == BIAS_UNAVAILABLE


# --------------------------------------------------------------------------
# 缺失 Agent 的显式标注（PRD 32.4）
# --------------------------------------------------------------------------


def test_missing_agents_are_explicitly_annotated() -> None:
    """缺失的 Agent 必须**显式出现在报告里**（PRD 32.4）。

    断言的是**具体那句话**，不是「文本里出现了 agent 名」——
    因为「数据缺口」那条也会提到 agent 名，
    只查名字的话，即使「未能产出结论」那句被删掉，用例**照样通过**。
    """
    content = master_to_report_content(
        _output(),
        missing_agents=["derivatives", "liquidity"],
    )
    joined = " ".join(content.uncertainties)
    assert "未能产出结论" in joined, "必须明确说出「哪个 Agent 没给出结论」"
    assert "derivatives" in joined
    assert "liquidity" in joined


def test_missing_agents_also_land_in_uncertainties_as_data_gap() -> None:
    """缺失 Agent 也要以「数据缺口」的形式出现在 ``uncertainties``。

    **注意**：``AnalysisReportContent`` **没有** ``missing_data`` 字段
    （``extra="forbid"``，多了会被 Pydantic 拒绝）。
    早先的实现算了一个 ``missing_data`` 局部变量然后**直接丢掉**——
    Master 报告的数据缺口与缺失 Agent 名单**全部消失**，
    而 PRD 32.4 要求「如实登记缺失」：
    **算完不落库等于没登记**。
    """
    content = master_to_report_content(
        _output(),
        missing_agents=["derivatives"],
    )
    joined = " ".join(content.uncertainties)
    assert "derivatives" in joined
    assert "数据缺口" in joined


def test_master_reported_missing_data_is_not_dropped() -> None:
    """Master 自己报告的数据缺口也必须落到报告里——不能被静默吞掉。"""
    content = master_to_report_content(
        _output(missing_data=["资金费率历史不足 30 条"]),
    )
    joined = " ".join(content.uncertainties)
    assert "资金费率历史不足 30 条" in joined


def test_no_missing_agents_leaves_uncertainties_untouched() -> None:
    """没有缺失时**不能凭空造**缺失声明。"""
    content = master_to_report_content(_output(uncertainties=["原有不确定性"]))
    joined = " ".join(content.uncertainties)
    assert "原有不确定性" in joined
    assert "未能产出结论" not in joined


# --------------------------------------------------------------------------
# 不得虚构证据引用（PRD 4.7.1）
# --------------------------------------------------------------------------


def test_no_evidence_refs_are_fabricated() -> None:
    """Master 的自然语言证据**不得**被转成 ``EvidenceRef``。

    ``EvidenceRef`` 要求精确的 ``source_path`` 与 ``value``——
    Master 给不出这些，硬转会造出**无法定位的引用**，
    而 PRD 4.7.1 明令「禁止虚构引用」。
    """
    content = master_to_report_content(
        _output(supporting_evidence=["价格位于 200 日均线上方", "资金费率为正"])
    )
    assert content.evidence == []
    assert content.counter_evidence == []


# --------------------------------------------------------------------------
# 情景与概率
# --------------------------------------------------------------------------


def test_scenarios_have_no_probability_when_none_provided() -> None:
    """没给概率就**不能编一个**。

    PRD 4.7.1：「概率如提供则定义互斥事件并归一化」——
    没提供就不能造。
    """
    content = master_to_report_content(_output(scenario_probabilities=None))
    assert content.scenario_probabilities is None
    assert content.scenarios
    assert all(s.probability is None for s in content.scenarios)


def test_scenario_probabilities_are_carried_through() -> None:
    content = master_to_report_content(
        _output(scenario_probabilities={"up": 0.5, "down": 0.3, "flat": 0.2})
    )
    assert content.scenario_probabilities is not None
    assert content.scenario_probabilities.up == 0.5
    assert content.scenario_probabilities.down == 0.3
    assert content.scenario_probabilities.flat == 0.2


def test_scenarios_carry_invalidation_conditions() -> None:
    """失效条件必须带到情景上——否则情景无法被跟踪。"""
    content = master_to_report_content(_output(invalidation_conditions=["跌破 19500"]))
    assert content.invalidation_conditions == ["跌破 19500"]
    assert all("跌破 19500" in s.invalidation_conditions for s in content.scenarios)


# --------------------------------------------------------------------------
# Master 保留的分歧要进报告
# --------------------------------------------------------------------------


def test_master_retained_conflict_lands_in_disagreements() -> None:
    """Master 保留的分歧必须进 ``disagreements``——读者得看见矛盾。"""
    content = master_to_report_content(
        _output(contradicting_evidence=["技术面与衍生品信号存在分歧"])
    )
    assert any("分歧" in item for item in content.disagreements)
