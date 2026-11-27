"""Master 输出 → 报告内容的转换（PRD 4.7.1 / 4.7.2）。

**Master 的输出契约（``AgentOutput``）与报告契约（``AnalysisReportContent``）
是两个不同的东西**，这个模块是它们之间的转换层。

**为什么需要转换层，而不是直接塞**：
- ``AgentOutput`` 有 15 个字段，含 ``confidence`` / ``findings`` /
  ``supporting_evidence`` 等**Agent 视角**的东西；
- ``AnalysisReportContent`` 是**用户视角**的：
  ``market_bias`` / ``dominant_factors`` / ``scenarios`` / ``evidence`` 等。

直接塞会得到一份「把 Agent 输出改个名字」的报告——
那正是 PRD 4.7.2 禁止 Master 做的事（「不能仅拼接子报告」）的**下游版本**。

**关键纪律：Master 失败时**不产出**报告内容**。
PRD 32.4：不得伪造输出。
Master 没给出结论，就没有结论——不能拿子 Agent 的输出拼一份出来。
那种拼出来的报告会让读者以为「多 Agent 达成了一致」，
而实际上 Master 根本没做综合。

**事实/推断/假设的标注被保留**：
Master 的 ``findings`` 用 ``【事实】`` / ``【推断】`` / ``【假设】`` 前缀，
转换时**原样保留**到 ``dominant_factors``——
标注是 Master 履行「区分事实、推断和待验证假设」这条纪律的**证据**，
把它洗掉等于让那条纪律失效。
"""

from __future__ import annotations

from aicrypto.domain.analysis.agent_output import AgentOutput
from aicrypto.domain.analysis.report import (
    AnalysisReportContent,
    AnalysisScenario,
    ScenarioProbability,
)

#: Master 用来标注证据类型的三个前缀（PRD 4.7.2）。
FACT_MARK = "【事实】"
INFERENCE_MARK = "【推断】"
HYPOTHESIS_MARK = "【假设】"

#: 方向词 → ``market_bias`` 取值。
_BIAS_UP = ("看涨", "上涨", "偏多", "做多", "多头", "上行", "bullish")
_BIAS_DOWN = ("看跌", "下跌", "偏空", "做空", "空头", "下行", "bearish")
_BIAS_FLAT = ("震荡", "横盘", "观望", "中性", "无明确", "不明确", "区间", "wait")

#: **没有把握时必须用 ``UNAVAILABLE``，绝不能用 ``NEUTRAL``**。
#:
#: ``NEUTRAL`` 是「我看下来是震荡」——一个有方向的判断。
#: ``UNAVAILABLE`` 是「我没结论」。
#: 把两者混同，会让「没分析出来」在报表上看起来像「分析出来是震荡」。
#: 这正是 PRD 24.4.1「部分结果红线」要求区分的东西。
BIAS_UNAVAILABLE = "UNAVAILABLE"
BIAS_NEUTRAL = "NEUTRAL"


def _split_marked_findings(findings: list[str]) -> tuple[list[str], list[str], list[str]]:
    """把 Master 的 ``findings`` 按【事实】/【推断】/【假设】拆开。

    没标注的归入「未标注」——**不强行分类**。
    强行分类等于替 Master 做了它没做的判断。
    """
    facts: list[str] = []
    inferences: list[str] = []
    hypotheses: list[str] = []
    unclassified: list[str] = []
    for finding in findings:
        if finding.startswith(FACT_MARK):
            facts.append(finding[len(FACT_MARK) :].strip())
        elif finding.startswith(INFERENCE_MARK):
            inferences.append(finding[len(INFERENCE_MARK) :].strip())
        elif finding.startswith(HYPOTHESIS_MARK):
            hypotheses.append(finding[len(HYPOTHESIS_MARK) :].strip())
        else:
            unclassified.append(finding)
    return facts, inferences, hypotheses + unclassified


def _detect_bias(text: str) -> str:
    """从 Master 的摘要里提取 ``market_bias``。

    **识别不出时返回 ``UNAVAILABLE``**，绝不返回 ``NEUTRAL``。
    """
    lowered = text.lower()
    up = any(word in lowered for word in _BIAS_UP)
    down = any(word in lowered for word in _BIAS_DOWN)
    flat = any(word in lowered for word in _BIAS_FLAT)
    matched = [name for name, hit in (("UP", up), ("DOWN", down), ("FLAT", flat)) if hit]
    if len(matched) != 1:
        return BIAS_UNAVAILABLE
    direction = matched[0]
    return {"UP": "BULLISH", "DOWN": "BEARISH", "FLAT": BIAS_NEUTRAL}[direction]


def _build_scenarios(output: AgentOutput) -> list[AnalysisScenario]:
    """从 Master 的结论构造情景。

    ``scenario_probabilities`` 存在时，按其分配概率；
    不存在时**不给概率**——PRD 4.7.1 说「概率如提供则定义互斥事件并归一化」，
    没提供就不能编一个。
    """
    probabilities = output.scenario_probabilities
    scenarios: list[AnalysisScenario] = []
    names = [("上行", "up"), ("下行", "down"), ("震荡", "flat")] if probabilities else []
    for label, key in names:
        value = probabilities.get(key) if probabilities else None
        scenarios.append(
            AnalysisScenario(
                name=label,
                probability=value,
                time_horizon=output.time_horizon,
                description=f"{label}情景（由 Master 综合各 Agent 结论得出）",
                trigger_conditions=[],
                invalidation_conditions=list(output.invalidation_conditions),
            )
        )
    if not scenarios:
        # 没有概率时，也要有一个**不带概率**的情景描述——
        # 否则报告里情景一栏是空的，读者看不到任何前瞻。
        scenarios.append(
            AnalysisScenario(
                name="主情景",
                probability=None,
                time_horizon=output.time_horizon,
                description=output.summary,
                trigger_conditions=[],
                invalidation_conditions=list(output.invalidation_conditions),
            )
        )
    return scenarios


def master_to_report_content(
    output: AgentOutput,
    *,
    missing_agents: list[str] | None = None,
) -> AnalysisReportContent:
    """把 Master 的输出转换成报告内容。

    ``missing_agents`` 是**缺失的 Agent 名单**——
    它会被显式写进 ``uncertainties``，
    因为 PRD 32.4 要求「在 Master 汇总中显式标注该 Agent 缺失」。
    """
    missing = list(missing_agents or [])
    facts, inferences, hypotheses = _split_marked_findings(output.findings)

    uncertainties = list(output.uncertainties)
    if missing:
        uncertainties.append(f"以下 Agent 未能产出结论，其视角缺失：{'、'.join(missing)}")

    missing_data = list(output.missing_data)
    for agent in missing:
        missing_data.append(f"{agent} 的输入缺失或输出不可用")

    dominant_factors = [f"{FACT_MARK}{item}" for item in facts[:5]] + [
        f"{INFERENCE_MARK}{item}" for item in inferences[:5]
    ]

    # 分歧：Master 保留的分歧要进报告的 ``disagreements``。
    disagreements = [
        item
        for item in output.contradicting_evidence
        if "分歧" in item or "冲突" in item or "不一致" in item
    ]
    disagreements.extend(f"{HYPOTHESIS_MARK}{item}" for item in hypotheses[:3])

    scenario_probabilities: ScenarioProbability | None = None
    if output.scenario_probabilities is not None:
        probabilities = output.scenario_probabilities
        scenario_probabilities = ScenarioProbability(
            up=probabilities["up"],
            down=probabilities["down"],
            flat=probabilities["flat"],
        )

    return AnalysisReportContent(
        summary=output.summary,
        market_bias=_detect_bias(output.summary),
        time_horizon=output.time_horizon,
        dominant_factors=dominant_factors,
        timeframe_views={},
        scenarios=_build_scenarios(output),
        scenario_probabilities=scenario_probabilities,
        # **证据引用为空**：Master 的 ``supporting_evidence`` 是
        # 「结论 + 独立来源数」的自然语言描述，
        # **不是** ``EvidenceRef``（它要求精确的 ``source_path`` 与 ``value``）。
        # 硬塞会造出**无法定位的证据引用**——
        # 而 PRD 4.7.1 明令「禁止虚构引用」。
        evidence=[],
        counter_evidence=[],
        disagreements=disagreements[:5],
        key_levels=[],
        watch_conditions=[],
        changes_since_previous=None,
        uncertainties=uncertainties[:5],
        invalidation_conditions=list(output.invalidation_conditions)[:5],
        prediction_ids=[],
    )


__all__ = [
    "BIAS_NEUTRAL",
    "BIAS_UNAVAILABLE",
    "FACT_MARK",
    "HYPOTHESIS_MARK",
    "INFERENCE_MARK",
    "master_to_report_content",
]
