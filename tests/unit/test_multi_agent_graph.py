"""多 Agent 编排的测试（PRD 4.7 / 24.4）。

**背景**：`agent_runs` / `agent_outputs` 两张表早已存在，
``agent_runs`` 只有**一个**写入方且**只在失败路径**上写，
``agent_outputs`` **零写入方**。
与 L-30 同族，但更隐蔽：表结构、索引、迁移全都齐备，
从「表存在」这个事实看**一切正常**。

**这些测试锁死的是「纪律」，不是「实现细节」**：

1. **子 Agent 互相不可见**（PRD 4.7 协作原则 1 / 24.4）——
   这不是「省 token」，是**正确性**要求。
   若 Technical 能看到 Derivatives 的结论，
   它会顺着已形成的解释走，6 个 Agent 退化成 1 个 Agent 调了 6 次，
   而且**看起来**做了独立验证、实际没有——这会**虚增**证据独立性。
2. **同源证据不得重复计为独立支持**（PRD 4.7.2 末段）——
   6 个 Agent 读同一份行情，``ohlcv`` 域内的 3 个 Agent
   **不是 3 份独立证据**，是 1 份。
3. **失败绝不伪造输出**（PRD 32.4）——
   失败的 Agent 必须进「缺失名单」，Master 必须知道缺了一块。
4. **方向性分歧要被识别**（PRD 4.7 协作原则 3）——
   分歧必须先被识别才能被保留。

**图不碰网络**：LLM 调用是可注入的，
因此拓扑与纪律可以在**不跑网络**的情况下被完整验证——
而拓扑错误（比如子 Agent 互相可见）恰恰是**最难在集成测试里发现**的。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from aicrypto.domain.analysis.agent_output import AgentOutput
from aicrypto.services.agent_registry import (
    ALL_AGENTS,
    MASTER_SPEC,
    SUB_AGENT_NAMES,
    SUB_AGENTS,
    independent_support_count,
    independent_support_groups,
)
from aicrypto.services.multi_agent import (
    AgentCallContext,
    AgentResult,
    agent_snapshot_view,
    detect_conflicts,
    detect_direction,
    parse_agent_output,
    render_master_messages,
    render_sub_agent_messages,
    run_multi_agent,
)

# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------

SNAPSHOT: dict[str, Any] = {
    "snapshot_id": "snap_test",
    "symbol": "BTCUSDT",
    "as_of": "2026-11-27T00:00:00Z",
    "available_as_of": "2026-11-27T00:00:00Z",
    "current_price": {"last": 100.0},
    "multi_timeframe_ohlcv": {"1h": []},
    "indicators": {"1h": {"rsi": 55.0}},
    "market_structure": {"1h": {}},
    "volatility_and_volume": {"1h": {}},
    "derivatives": {"open_interest": 1},
    "liquidity": {"orderbook": {}},
    "market_regime": {"1h": {"regime": "RANGE"}},
    "data_quality": {"overall": "GOOD"},
}


def _output(agent_name: str, summary: str) -> AgentOutput:
    return AgentOutput(
        agent_name=agent_name,
        agent_version="v1",
        model_id="test-model",
        prompt_version="v1",
        market_snapshot_id="snap_test",
        as_of="2026-11-27T00:00:00Z",
        time_horizon="4h",
        summary=summary,
        findings=["一条发现"],
        confidence=0.6,
    )


def _result(agent_name: str, summary: str = "证据不足，观望") -> AgentResult:
    return AgentResult(
        agent_name=agent_name,
        status="SUCCESS",
        output=_output(agent_name, summary),
        model_id="test-model",
        prompt_version="v1",
        tier="TIER_LIGHT",
    )


class _Recorder:
    """记录每次调用的 invoker。"""

    def __init__(self, summaries: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, list[dict[str, str]]]] = []
        self._summaries = summaries or {}

    def __call__(
        self,
        spec: Any,
        messages: list[dict[str, str]],
        prompt_version: str,
        context: AgentCallContext,
    ) -> AgentResult:
        self.calls.append((spec.name, messages))
        summary = self._summaries.get(spec.name, "证据不足，观望")
        return AgentResult(
            agent_name=spec.name,
            status="SUCCESS",
            output=_output(spec.name, summary),
            model_id="test-model",
            prompt_version=prompt_version,
            tier=spec.tier,
        )


def _run(recorder: _Recorder) -> Any:
    return run_multi_agent(
        snapshot=SNAPSHOT,
        snapshot_id="snap_test",
        as_of="2026-11-27T00:00:00Z",
        time_horizon="4h",
        invoker=recorder,
    )


# --------------------------------------------------------------------------
# 注册表
# --------------------------------------------------------------------------


def test_six_sub_agents() -> None:
    """PRD 4.7 的 6 个 Trading 子 Agent 全部就位。"""
    assert len(SUB_AGENTS) == 6
    assert set(SUB_AGENT_NAMES) == {
        "technical",
        "structure",
        "derivatives",
        "liquidity",
        "regime",
        "risk",
    }


def test_master_is_separate_from_sub_agents() -> None:
    """Master **不在**子 Agent 列表里——它是 fan-in 之后的另一个角色。"""
    assert MASTER_SPEC.name == "master"
    assert "master" not in SUB_AGENT_NAMES
    assert len(ALL_AGENTS) == 7


def test_every_agent_has_question_and_boundary() -> None:
    """每个 Agent 都必须声明自己的**独立问题**与**职责边界**。

    PRD 4.7.2 用「必须回答的独立问题」与「职责边界」两列区分 Agent。
    没有边界的 Agent 会**越界**——
    比如 Technical 给出关键价位、Regime 罗列技术指标，
    那会造成**同源证据重复**，进而虚增置信度。
    """
    for spec in ALL_AGENTS:
        assert spec.question.strip(), f"{spec.name} 缺少独立问题"
        assert spec.boundary.strip(), f"{spec.name} 缺少职责边界"


def test_every_agent_has_snapshot_view() -> None:
    """每个 Agent 都必须有快照视图（PRD 24.4「各自独立快照视图」）。"""
    from aicrypto.domain.analysis.snapshot import AGENT_VIEWS

    for spec in ALL_AGENTS:
        assert spec.snapshot_view in AGENT_VIEWS, f"{spec.name} 的视图键不存在"


# --------------------------------------------------------------------------
# 核心纪律：子 Agent 互相不可见
# --------------------------------------------------------------------------


def test_sub_agents_cannot_see_each_other() -> None:
    """**子 Agent 的输入里绝不能包含其他分支的东西。**

    这是 PRD 4.7 协作原则 1 与 24.4 的核心。
    若 Technical 能看到 Derivatives 的结论，
    6 个 Agent 就退化成 1 个 Agent 调了 6 次。

    **断言「精确的键集合」而不是「不含某几个词」**——
    只断言「不含 findings / summary」太弱：
    快照块里根本没有这些键，断言会**恒真**，
    于是「分支拿到了全量快照」这个缺陷根本锁不住。
    精确键集合断言才能真正锁住它。
    """
    from aicrypto.domain.analysis.snapshot import AGENT_VIEWS

    recorder = _Recorder({"technical": "技术面偏多，趋势向上"})
    _run(recorder)

    metadata = {"snapshot_id", "symbol", "as_of", "available_as_of"}
    for name, messages in recorder.calls:
        if name == "master":
            continue
        view = json.loads(messages[1]["content"])
        expected = metadata | (set(AGENT_VIEWS[name]) & set(SNAPSHOT))
        assert set(view) == expected, (
            f"{name} 的输入视图不对：多出 {set(view) - expected}，缺少 {expected - set(view)}"
        )
        # 别的 Agent 的视角块**不得**出现
        for other in SUB_AGENT_NAMES:
            if other == name:
                continue
            for block in AGENT_VIEWS[other] - AGENT_VIEWS[name]:
                assert block not in view, f"{name} 的输入里混进了 {other} 的 {block}"


def test_master_sees_all_agent_reports() -> None:
    """Master **必须**看到全部子 Agent 的结论——否则它无从综合。"""
    recorder = _Recorder()
    _run(recorder)
    master_messages = dict(recorder.calls)["master"]
    content = master_messages[1]["content"]
    for agent_name in SUB_AGENT_NAMES:
        assert agent_name in content, f"Master 没收到 {agent_name} 的报告"


def test_each_sub_agent_gets_its_own_trimmed_view() -> None:
    """每个子 Agent 只能看到**自己职责相关**的快照块。"""
    recorder = _Recorder()
    _run(recorder)

    views = {
        name: json.loads(messages[1]["content"])
        for name, messages in recorder.calls
        if name != "master"
    }

    assert "indicators" in views["technical"]
    assert "indicators" not in views["derivatives"]
    assert "market_structure" in views["structure"]
    assert "market_structure" not in views["technical"]
    assert "derivatives" in views["derivatives"]
    assert "derivatives" not in views["liquidity"]
    assert "liquidity" in views["liquidity"]
    assert "liquidity" not in views["derivatives"]


def test_snapshot_view_keeps_provenance_metadata() -> None:
    """裁剪视图**必须保留** ``snapshot_id`` 与 ``as_of``。

    溯源字段是必需的：Agent 要能引用本次快照 ID，
    证据引用必须指向本次 snapshot_id（PRD 4.7）。
    """
    view = agent_snapshot_view(SNAPSHOT, "technical")
    assert view["snapshot_id"] == "snap_test"
    assert view["as_of"] == "2026-11-27T00:00:00Z"
    assert view["symbol"] == "BTCUSDT"


def test_unknown_view_returns_full_snapshot() -> None:
    """未知视图键返回全量——不静默丢块。"""
    view = agent_snapshot_view(SNAPSHOT, "nonexistent")
    assert "indicators" in view
    assert "derivatives" in view


# --------------------------------------------------------------------------
# 同源证据不得重复计为独立支持（PRD 4.7.2）
# --------------------------------------------------------------------------


def test_ohlcv_domain_agents_are_not_independent() -> None:
    """``ohlcv`` 域内的 3 个 Agent **不是 3 份独立证据**。

    技术面、市场结构、市场状态读的是**同一批 K 线**——
    它们是同一份事实的不同切面，不是三个独立来源。
    """
    groups = independent_support_groups(list(SUB_AGENTS))
    assert set(groups["ohlcv"]) == {"technical", "structure", "regime"}
    assert len(groups["ohlcv"]) == 3


def test_independent_support_count_is_by_domain() -> None:
    """独立支持数按**数据来源域**计，不按 Agent 数。"""
    ohlcv_only = [spec for spec in SUB_AGENTS if spec.evidence_domains == ("ohlcv",)]
    assert len(ohlcv_only) == 3
    assert independent_support_count(ohlcv_only) == 1, "3 个同源 Agent 只算 1 个独立来源"

    all_specs = list(SUB_AGENTS)
    assert independent_support_count(all_specs) == 4


def test_evidence_groups_produced_by_graph() -> None:
    """图产出给 Master 的分组必须体现「同源不算独立」。"""
    recorder = _Recorder()
    state = _run(recorder)
    groups = state["evidence_groups"]
    assert set(groups["ohlcv"]) == {"technical", "structure", "regime"}
    assert groups["derivatives"] == ["derivatives"]
    assert groups["liquidity"] == ["liquidity"]
    assert groups["quality"] == ["risk"]


def test_master_receives_evidence_groups() -> None:
    """Master 的 Prompt 里**必须**带证据分组。

    「同源指标不得重复计为独立支持」这条纪律，
    Master 需要**事实依据**才能执行——
    若不告诉它哪些 Agent 同源，它只能凭感觉判断。
    """
    recorder = _Recorder()
    _run(recorder)
    master_content = dict(recorder.calls)["master"][1]["content"]
    assert "ohlcv" in master_content
    assert "derivatives" in master_content


# --------------------------------------------------------------------------
# 分歧检测（PRD 4.7 协作原则 3）
# --------------------------------------------------------------------------


def test_detect_direction_up_down_flat() -> None:
    assert detect_direction("技术面偏多，趋势向上") == "UP"
    assert detect_direction("结构破位，看跌") == "DOWN"
    assert detect_direction("震荡整理，观望") == "FLAT"


def test_detect_direction_returns_none_when_unclear() -> None:
    """识别不出方向时返回 ``None``——**宁可漏报分歧**。"""
    assert detect_direction("多空不明") is None
    assert detect_direction("") is None


def test_detect_direction_returns_none_when_both_directions() -> None:
    """同时提到多空 = **识别不出**，不是「矛盾」。"""
    assert detect_direction("上行但也可能下行") is None


def test_opposite_directions_are_a_conflict() -> None:
    """明确相反的方向 = 分歧。"""
    results = [
        _result("technical", "趋势向上，看涨"),
        _result("structure", "结构破位，看跌"),
    ]
    conflicts = detect_conflicts(results)
    assert len(conflicts) == 1
    assert set(conflicts[0].agents) == {"technical", "structure"}
    assert conflicts[0].kind == "DIRECTION"


def test_only_opposite_pairs_are_conflicts() -> None:
    """**只有相反的方向对**才算冲突——同向的那一对不算。

    这个用例需要**三个** Agent：
    两个 Agent 时，「检测不到任何冲突」与「检测到恰好一个冲突」
    在用例里可能长得一样；
    三个 Agent 才能区分
    「只识别真正相反的那一对」与「把所有方向不同的都当成冲突」。

    这里 technical(UP) / structure(DOWN) / regime(UP)：
    - ``(technical, structure)`` 相反 -> 是冲突；
    - ``(structure, regime)`` 相反 -> 也是冲突；
    - ``(technical, regime)`` 同向 -> **不是**冲突。
    """
    results = [
        _result("technical", "趋势向上，看涨"),
        _result("structure", "结构破位，看跌"),
        _result("regime", "环境偏多，看涨"),
    ]
    conflicts = detect_conflicts(results)
    pairs = {frozenset(c.agents) for c in conflicts}

    assert frozenset({"technical", "structure"}) in pairs
    assert frozenset({"structure", "regime"}) in pairs
    assert frozenset({"technical", "regime"}) not in pairs, "同向的两位不该被算作冲突"
    assert len(conflicts) == 2, f"只应有两对相反方向，实际 {len(conflicts)}：{conflicts}"


def test_flat_is_not_a_conflict_with_directional() -> None:
    """``UP`` vs ``FLAT`` **不是**分歧。

    「证据不足、观望」与「部分证据偏多」**并不矛盾**——
    证据不足完全可以与「已看到的证据偏多」共存。
    若把它算作分歧，6 个 Agent 里一个方向明确、一个保守
    就会产出一堆**假分歧**，淹没真分歧，
    而 PRD 4.7 协作原则 3 要求保留的是「**重大**分歧」。
    """
    results = [
        _result("technical", "趋势向上，看涨"),
        _result("structure", "证据不足，观望"),
    ]
    assert detect_conflicts(results) == []


def test_same_direction_is_not_a_conflict() -> None:
    results = [
        _result("technical", "趋势向上，看涨"),
        _result("regime", "环境偏多，看涨"),
    ]
    assert detect_conflicts(results) == []


def test_failed_agents_do_not_create_conflicts() -> None:
    """失败的 Agent **不参与**分歧判定——它没有结论。"""
    failed = AgentResult(agent_name="technical", status="FAILED", output=None)
    results = [failed, _result("structure", "看跌")]
    assert detect_conflicts(results) == []


def test_conflicts_are_detected_in_graph_run() -> None:
    recorder = _Recorder(
        {
            "technical": "技术面偏多，趋势向上",
            "structure": "结构破位，看跌",
        }
    )
    state = _run(recorder)
    assert len(state["conflicts"]) == 1
    assert set(state["conflicts"][0].agents) == {"technical", "structure"}


# --------------------------------------------------------------------------
# 失败处理（PRD 32.4）
# --------------------------------------------------------------------------


def test_failed_agent_appears_in_missing_list() -> None:
    """失败的 Agent 必须进**缺失名单**——Master 要知道缺了一块证据。"""

    def invoker(spec, messages, prompt_version, context):
        if spec.name == "technical":
            return AgentResult(
                agent_name="technical",
                status="FAILED",
                output=None,
                error={"code": "LLM_UNAVAILABLE"},
                model_id="test-model",
                prompt_version=prompt_version,
                tier=spec.tier,
            )
        return AgentResult(
            agent_name=spec.name,
            status="SUCCESS",
            output=_output(spec.name, "证据不足，观望"),
            model_id="test-model",
            prompt_version=prompt_version,
            tier=spec.tier,
        )

    state = run_multi_agent(
        snapshot=SNAPSHOT,
        snapshot_id="snap_test",
        as_of="2026-11-27T00:00:00Z",
        time_horizon="4h",
        invoker=invoker,
    )
    assert state["missing_agents"] == ["technical"]
    assert len(state["agent_results"]) == 6, "失败的 Agent 也要计入结果，不能消失"


def test_failure_does_not_abort_the_run() -> None:
    """一个子 Agent 崩溃**不得**让整次分析失败。

    PRD 24.4：「超时则输出『分析超时 + 已完成部分』的部分结果，
    而不是整体失败」。
    若异常穿出去，其他 5 个 Agent 已经算好的结果就**全丢了**——
    而它们是真的花了钱、真的算出来了。
    """

    def invoker(spec, messages, prompt_version, context):
        if spec.name == "liquidity":
            raise RuntimeError("boom")
        return AgentResult(
            agent_name=spec.name,
            status="SUCCESS",
            output=_output(spec.name, "证据不足，观望"),
            model_id="test-model",
            prompt_version=prompt_version,
            tier=spec.tier,
        )

    state = run_multi_agent(
        snapshot=SNAPSHOT,
        snapshot_id="snap_test",
        as_of="2026-11-27T00:00:00Z",
        time_horizon="4h",
        invoker=invoker,
    )
    # 崩溃的 Agent 变成 FAILED，而不是让整次运行挂掉
    assert len(state["agent_results"]) == 6
    assert state["missing_agents"] == ["liquidity"]
    # 其余 5 个 Agent 的结果**保住了**
    succeeded = [r for r in state["agent_results"] if r.succeeded]
    assert len(succeeded) == 5
    # Master 照常跑完
    assert state["master_result"] is not None
    assert state["master_result"].succeeded


def test_master_failure_is_isolated_too() -> None:
    """Master 崩溃时，子 Agent 的结果**依然保留**。

    Master 失败 = 没有最终结论，但子 Agent 的输出是真的。
    让异常穿出去会让调用方丢掉全部信息。
    """

    def invoker(spec, messages, prompt_version, context):
        if spec.name == "master":
            raise RuntimeError("master boom")
        return AgentResult(
            agent_name=spec.name,
            status="SUCCESS",
            output=_output(spec.name, "证据不足，观望"),
            model_id="test-model",
            prompt_version=prompt_version,
            tier=spec.tier,
        )

    state = run_multi_agent(
        snapshot=SNAPSHOT,
        snapshot_id="snap_test",
        as_of="2026-11-27T00:00:00Z",
        time_horizon="4h",
        invoker=invoker,
    )
    assert state["master_result"] is not None
    assert not state["master_result"].succeeded
    assert state["master_result"].output is None, "Master 失败不得伪造输出"
    assert len(state["agent_results"]) == 6, "子 Agent 的结果必须保住"


def test_master_receives_missing_agent_list() -> None:
    """Master 的 Prompt 里必须含**缺失名单**。

    PRD 32.4：schema 修复失败时「在 Master 汇总中显式标注该 Agent 缺失，
    **不得伪造输出**」。
    """

    def invoker(spec, messages, prompt_version, context):
        if spec.name == "derivatives":
            return AgentResult(
                agent_name="derivatives",
                status="FAILED",
                output=None,
                error={"code": "LLM_SCHEMA_INVALID"},
                model_id="test-model",
                prompt_version=prompt_version,
                tier=spec.tier,
            )
        return AgentResult(
            agent_name=spec.name,
            status="SUCCESS",
            output=_output(spec.name, "证据不足，观望"),
            model_id="test-model",
            prompt_version=prompt_version,
            tier=spec.tier,
        )

    captured: list[list[dict[str, str]]] = []

    def capturing(spec, messages, prompt_version, context):
        captured.append(messages)
        return invoker(spec, messages, prompt_version, context)

    run_multi_agent(
        snapshot=SNAPSHOT,
        snapshot_id="snap_test",
        as_of="2026-11-27T00:00:00Z",
        time_horizon="4h",
        invoker=capturing,
    )
    master_content = captured[-1][1]["content"]
    assert "derivatives" in master_content, "Master 没收到缺失名单"


# --------------------------------------------------------------------------
# 输出解析
# --------------------------------------------------------------------------


def test_parse_agent_output_valid() -> None:
    payload = json.dumps(
        {
            "agent_name": "technical",
            "agent_version": "v1",
            "model_id": "m",
            "prompt_version": "v1",
            "market_snapshot_id": "s",
            "as_of": "2026-11-27T00:00:00Z",
            "time_horizon": "4h",
            "summary": "偏多",
            "confidence": 0.5,
        }
    )
    parsed = parse_agent_output(payload)
    assert parsed.agent_name == "technical"
    assert parsed.confidence == 0.5


def test_parse_agent_output_strips_markdown_fence() -> None:
    """模型有时会加 markdown 围栏——不该因此判为「不是 JSON」。

    围栏被剥掉后内容会被正常解析，
    随后因为缺少必填字段而校验失败——
    **失败原因是「字段缺失」而不是「JSON 非法」**。
    """
    payload = '```json\n{"agent_name":"a"}\n```'
    with pytest.raises(ValidationError) as excinfo:
        parse_agent_output(payload)
    assert "agent_name" in str(excinfo.value)


def test_parse_agent_output_rejects_invalid_json() -> None:
    with pytest.raises(ValueError):
        parse_agent_output("not json")


def test_parse_agent_output_rejects_root_array_of_many() -> None:
    with pytest.raises(ValueError):
        parse_agent_output("[{}, {}]")


def test_parse_agent_output_never_returns_empty_on_error() -> None:
    """解析失败必须抛——**绝不**返回空对象冒充成功。"""
    with pytest.raises(ValidationError):
        parse_agent_output("{}")


# --------------------------------------------------------------------------
# Prompt 渲染
# --------------------------------------------------------------------------


def test_sub_agent_prompt_includes_schema() -> None:
    """子 Agent 的 system 消息必须带 Schema。"""
    spec = SUB_AGENTS[0]
    messages, version = render_sub_agent_messages(spec, {"current_price": {}})
    assert version == "v1"
    assert "Schema" in messages[0]["content"]


def test_master_prompt_has_all_placeholders_filled() -> None:
    """Master 的 Prompt 不能有**未渲染的占位符**。

    ``PromptTemplate.render`` 对缺占位符会报错，
    因此这里渲染成功即说明三个占位符都填上了。
    """
    from aicrypto.services.multi_agent import EvidenceBundle

    bundle = EvidenceBundle(
        results=(_result("technical"),),
        conflicts=(),
        missing_agents=("liquidity",),
        evidence_groups={"ohlcv": ["technical"]},
    )
    messages, version = render_master_messages(bundle, SNAPSHOT)
    assert version == "v1"
    content = messages[1]["content"]
    assert "{evidence_groups}" not in content
    assert "{agent_reports}" not in content
    assert "{missing_agents}" not in content
    assert "{snapshot}" not in content
