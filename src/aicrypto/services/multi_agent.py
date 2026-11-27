"""多 Agent 编排（PRD 4.7 / 24.4）。

**拓扑**（PRD 24.4）::

    [Snapshot]
       |
       v
    [fan-out]  Technical / Structure / Derivatives / Liquidity / Regime / Risk
       |         (TIER_LIGHT，并行，各自独立快照视图，互相不可见)
       v
    [Aggregator]  合并为证据包，检测并标注冲突
       |
       v
    [Master]  汇总 + 分歧处理 + 情景概率
       |
       v
    [Persister]  写 agent_runs + agent_outputs

**用 LangGraph 的理由**：这个拓扑有**动态 fan-out**
（分支数由注册表决定，不是写死的 6 个）、
**并行分支**、以及**带 reducer 的并发写**。
手写 asyncio + 队列要自己处理「多个分支写同一个 state key」的问题，
而 LangGraph 的 ``Send`` + reducer 正是为此设计的。

**最关键的一条纪律：子 Agent 互相不可见**（PRD 4.7 协作原则 1 / 24.4）。

这不是「省 token」，而是**正确性**要求。
若 Technical 能看到 Derivatives 的结论，
它会倾向于**顺着**已形成的解释走——
6 个 Agent 退化成 1 个 Agent 调了 6 次：
成本翻 6 倍，换来的是同一个结论的 6 份拷贝。
更糟的是它**看起来**做了独立验证，
而实际上没有——这会**虚增**证据的独立性。

实现上这条纪律由 ``Send`` 传的**局部 state** 保证：
每个分支只带自己的快照视图，**不带**其他分支的输出。
这条性质由 ``tests/unit/test_multi_agent_graph.py`` 断言
（「分支输入里不含其他分支的结论」），不是靠约定。

**失败绝不伪造输出**（PRD 32.4）：
失败的 Agent 写 ``agent_runs``（status=FAILED），
**不写** ``agent_outputs``。
Master 收到的是「缺失名单」，会**显式标注该 Agent 缺失**——
而不是收到一个编造的结论。

**LLM 调用是可注入的**（``AgentInvoker``）：
图本身不碰 HTTP。真实调用由 ``services/llm_agent.py`` 提供，
测试则注入假实现。
这样「图的拓扑与纪律」可以在**不跑网络**的情况下被完整验证——
而拓扑错误（比如子 Agent 互相可见）恰恰是**最难在集成测试里发现**的。
"""

from __future__ import annotations

import json
import operator
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from aicrypto.common.errors import ErrorCode
from aicrypto.config.prompts import load_template
from aicrypto.domain.analysis.agent_output import AgentOutput
from aicrypto.domain.analysis.snapshot import AGENT_VIEWS
from aicrypto.infrastructure.logging import get_logger
from aicrypto.services.agent_registry import (
    MASTER_SPEC,
    SUB_AGENTS,
    AgentSpec,
    independent_support_groups,
)

logger = get_logger(__name__)

#: 子 Agent 的系统模板名
SUB_SYSTEM_TEMPLATE = "analysis"
#: Master 的系统模板名
MASTER_SYSTEM_TEMPLATE = "synthesis"
#: Master 的用户模板名
MASTER_USER_TEMPLATE = "synthesis.user"


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentResult:
    """一个 Agent 的执行结果。

    ``output`` 为 ``None`` 表示**失败或超时**——不是「输出为空」。
    这个区分很重要：空输出的 Agent 与失败的 Agent 在
    Master 那里**待遇不同**（前者是「没话说」，后者是「缺一块证据」）。
    """

    agent_name: str
    status: str
    output: AgentOutput | None
    error: dict[str, Any] | None = None
    duration_ms: int = 0
    model_id: str = ""
    prompt_version: str = ""
    tier: str = ""
    degraded: bool = False
    degraded_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "SUCCESS" and self.output is not None


@dataclass(frozen=True, slots=True)
class Conflict:
    """两个子 Agent 之间的**方向性分歧**。

    只在**结论方向相反**时才算冲突——
    「都认为证据不足」不是冲突，是一致。
    """

    agents: tuple[str, str]
    position_a: str
    position_b: str
    kind: str


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """汇总给 Master 的证据包。"""

    results: tuple[AgentResult, ...]
    conflicts: tuple[Conflict, ...]
    missing_agents: tuple[str, ...]
    #: 按数据来源域分组的 Agent 名单（独立性判定的事实依据）
    evidence_groups: dict[str, list[str]]

    @property
    def succeeded(self) -> tuple[AgentResult, ...]:
        return tuple(r for r in self.results if r.succeeded)

    @property
    def failed(self) -> tuple[AgentResult, ...]:
        return tuple(r for r in self.results if not r.succeeded)


#: LLM 调用接口：给定 Agent 与 messages，返回执行结果。
#:
#: 刻意做成**可注入**：图的拓扑与纪律（尤其是「子 Agent 互相不可见」）
#: 必须在**不跑网络**的情况下被验证。
#:
#: ``prompt_version`` 一并传入——它由**渲染时**确定，
#: 调用方若自己去重新读文件，会引入
#: 「渲染用的版本」与「记录用的版本」不一致的可能。
AgentInvoker = Callable[
    [AgentSpec, list[dict[str, str]], str, "AgentCallContext"],
    AgentResult,
]


@dataclass(frozen=True, slots=True)
class AgentCallContext:
    """一次 Agent 调用的上下文。"""

    snapshot_id: str
    as_of: str
    time_horizon: str
    tier: str


# --------------------------------------------------------------------------
# LangGraph state
# --------------------------------------------------------------------------


class MultiAgentState(TypedDict, total=False):
    """多 Agent 图的状态。

    **并发写同一个 key 必须配 reducer**——否则并发写入直接抛异常
    （``InvalidUpdateError``）。``agent_results`` 被 6 个分支同时写，
    因此用 ``Annotated[list[AgentResult], operator.add]``。
    """

    snapshot: dict[str, Any]
    snapshot_id: str
    as_of: str
    time_horizon: str
    #: 并发写入（**必须**配 reducer）
    agent_results: Annotated[list[AgentResult], operator.add]
    #: 节点名 -> 执行函数（``Send`` 用它把分支派发到具体节点）
    runners: dict[str, Callable[[MultiAgentState], AgentResult]]
    #: 分支级局部 state（``Send`` 传的 ``arg``）
    branch_input: dict[str, Any]
    conflicts: list[Conflict]
    evidence_groups: dict[str, list[str]]
    missing_agents: list[str]
    master_result: AgentResult | None
    master_output: AgentOutput | None


class GraphNode(Protocol):
    """LangGraph 节点函数的类型。

    **返回类型必须是 ``Any``**——LangGraph 允许节点返回
    「state 的部分更新」或「完整 state」两种形态，
    它的 ``_Node`` Protocol 用的就是 ``Any``。
    若写成 ``Callable[[State], dict[str, Any]]``，
    mypy 会因为返回类型不协变而拒绝 ``add_node``。
    """

    def __call__(self, state: MultiAgentState) -> Any: ...


# --------------------------------------------------------------------------
# 分歧检测
# --------------------------------------------------------------------------

#: 方向性关键词——用于从自然语言结论里提取「方向」。
#:
#: **这是一个保守的近似**：它只看明确的方向词，
#: 识别不出的情况一律**不算冲突**（宁可漏报也不误报）。
#: 误报冲突会让 Master 面对一堆假分歧，
#: 反而稀释了真分歧的信号——而 PRD 4.7 协作原则 3 要求
#: 「对**重大**分歧保留分歧」，假分歧会淹没真分歧。
_DIRECTION_UP = ("看涨", "上涨", "偏多", "做多", "多头", "上行", "bullish")
_DIRECTION_DOWN = ("看跌", "下跌", "偏空", "做空", "空头", "下行", "bearish")
_DIRECTION_FLAT = ("震荡", "横盘", "观望", "中性", "无明确", "不明确", "区间", "wait")


def detect_direction(text: str) -> str | None:
    """从一段中文结论里提取方向。

    返回 ``"UP"`` / ``"DOWN"`` / ``"FLAT"`` / ``None``（识别不出）。

    **保守**：识别不出就返回 ``None``，
    调用方**不**把它当成冲突——宁可漏报分歧。
    """
    lowered = text.lower()
    hits = {
        "UP": any(word in lowered for word in _DIRECTION_UP),
        "DOWN": any(word in lowered for word in _DIRECTION_DOWN),
        "FLAT": any(word in lowered for word in _DIRECTION_FLAT),
    }
    matched = [direction for direction, hit in hits.items() if hit]
    if len(matched) != 1:
        # 0 个 = 识别不出；多个 = 自相矛盾或同时提到多空。
        # 两种情况都**不**当作明确方向。
        return None
    return matched[0]


def detect_conflicts(results: Sequence[AgentResult]) -> list[Conflict]:
    """在成功的 Agent 结果之间检测**方向性分歧**。

    PRD 4.7 协作原则 3 要求 Master「对重大分歧保留分歧」。
    分歧必须**先被识别**才能被保留——
    否则 Master 只能靠自己从子报告里看出来，
    而模型很容易在汇总时把分歧「抹平」。

    **只有「明确相反的方向」才算分歧**。
    ``UP`` vs ``DOWN`` 是分歧；
    ``UP`` vs ``FLAT`` **不是**——
    后者是「一个 Agent 给了方向、另一个说证据不足」，
    两者并不矛盾：证据不足本身完全可以与「部分证据偏多」共存。
    若把它算作分歧，6 个 Agent 里只要有一个方向明确、一个保守，
    就会产出一堆「假分歧」——
    而 PRD 4.7 协作原则 3 要求保留的是「**重大**分歧」。
    假分歧会淹没真分歧，Master 面对一堆噪音反而可能忽略真正的对立。
    """
    succeeded = [r for r in results if r.succeeded]
    conflicts: list[Conflict] = []
    for index, left in enumerate(succeeded):
        left_output = left.output
        if left_output is None:
            continue
        for right in succeeded[index + 1 :]:
            right_output = right.output
            if right_output is None:
                continue
            left_direction = detect_direction(left_output.summary)
            right_direction = detect_direction(right_output.summary)
            if left_direction is None or right_direction is None:
                continue
            if not _is_opposite(left_direction, right_direction):
                continue
            conflicts.append(
                Conflict(
                    agents=(left.agent_name, right.agent_name),
                    position_a=left_output.summary,
                    position_b=right_output.summary,
                    kind="DIRECTION",
                )
            )
    return conflicts


def _is_opposite(left: str, right: str) -> bool:
    """两个方向是否**明确相反**。

    只有 ``UP`` vs ``DOWN`` 算相反。
    ``FLAT``（证据不足 / 观望）**不与任何方向构成对立**——
    它是「不下判断」，不是「判断为反向」。
    """
    return {left, right} == {"UP", "DOWN"}


# --------------------------------------------------------------------------
# 快照视图
# --------------------------------------------------------------------------


def agent_snapshot_view(snapshot: dict[str, Any], view: str) -> dict[str, Any]:
    """按 Agent 视图裁剪快照。

    ``snapshot`` 是 ``analysis_worker.build_snapshot`` 的返回值——
    它的**语义块在顶层**（``indicators`` / ``market_structure`` / …），
    另有 ``snapshot_id`` / ``symbol`` / ``as_of`` 等元信息。

    裁剪时**保留元信息**（``snapshot_id`` 与 ``as_of`` 是溯源必需的，
    Agent 要能引用本次快照 ID），只裁掉**语义块**。

    裁剪逻辑复用 ``domain/analysis/snapshot.py::AGENT_VIEWS``——
    那是「各 Agent 可见什么」的唯一真源（PRD 24.4「各自独立快照视图」）。
    """
    allowed = AGENT_VIEWS.get(view)
    if allowed is None:
        return snapshot
    metadata: dict[str, Any] = {
        key: snapshot[key]
        for key in ("snapshot_id", "symbol", "as_of", "available_as_of")
        if key in snapshot
    }
    blocks = {key: value for key, value in snapshot.items() if key in allowed}
    return {**metadata, **blocks}


# --------------------------------------------------------------------------
# Prompt 渲染
# --------------------------------------------------------------------------


def render_sub_agent_messages(
    spec: AgentSpec,
    snapshot_view: dict[str, Any],
) -> tuple[list[dict[str, str]], str]:
    """渲染子 Agent 的 messages，返回 ``(messages, prompt_version)``。"""
    system_template = load_template(spec.prompt_dir, SUB_SYSTEM_TEMPLATE)
    schema = AgentOutput.model_json_schema()
    messages = [
        {
            "role": "system",
            "content": system_template.render(schema=json.dumps(schema, ensure_ascii=False)),
        },
        {
            "role": "user",
            "content": json.dumps(snapshot_view, ensure_ascii=False),
        },
    ]
    return messages, system_template.version


def render_master_messages(
    bundle: EvidenceBundle,
    snapshot: dict[str, Any],
) -> tuple[list[dict[str, str]], str]:
    """渲染 Master 的 messages。

    **三个占位符都是必填的**——
    尤其 ``evidence_groups``：它是「同源指标不得重复计为独立支持」
    这条纪律的**事实依据**。若不传，Master 只能凭感觉判断独立性。
    """
    system_template = load_template(MASTER_SPEC.prompt_dir, MASTER_SYSTEM_TEMPLATE)
    user_template = load_template(MASTER_SPEC.prompt_dir, MASTER_USER_TEMPLATE)
    schema = AgentOutput.model_json_schema()

    reports = []
    for result in bundle.succeeded:
        output = result.output
        if output is None:
            continue
        reports.append(
            {
                "agent_name": output.agent_name,
                "summary": output.summary,
                "findings": output.findings,
                "supporting_evidence": output.supporting_evidence,
                "contradicting_evidence": output.contradicting_evidence,
                "uncertainties": output.uncertainties,
                "missing_data": output.missing_data,
                "confidence": output.confidence,
                "invalidation_conditions": output.invalidation_conditions,
            }
        )

    messages = [
        {
            "role": "system",
            "content": system_template.render(schema=json.dumps(schema, ensure_ascii=False)),
        },
        {
            "role": "user",
            "content": user_template.render(
                evidence_groups=json.dumps(bundle.evidence_groups, ensure_ascii=False, indent=2),
                agent_reports=json.dumps(reports, ensure_ascii=False, indent=2),
                missing_agents=json.dumps(list(bundle.missing_agents), ensure_ascii=False),
                snapshot=json.dumps(snapshot, ensure_ascii=False),
            ),
        },
    ]
    return messages, system_template.version


# --------------------------------------------------------------------------
# 输出解析
# --------------------------------------------------------------------------


def parse_agent_output(raw: str) -> AgentOutput:
    """解析并校验 Agent 的 JSON 输出。

    校验失败抛 :class:`ValueError` 或 :class:`ValidationError`——
    由调用方决定是修复重试还是记 FAILED。
    **绝不返回空对象冒充成功**。
    """
    text = raw.strip()
    if text.startswith("```"):
        # 去掉 markdown 代码围栏——模型有时会加。
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Agent 输出不是合法 JSON：{exc}") from exc
    if isinstance(data, list):
        if len(data) == 1:
            data = data[0]
        else:
            raise ValueError("Agent 输出根节点是数组且长度不为 1")
    if not isinstance(data, dict):
        raise ValueError("Agent 输出根节点不是对象")
    return AgentOutput.model_validate(data)


# --------------------------------------------------------------------------
# 图
# --------------------------------------------------------------------------


def build_graph(invoker: AgentInvoker) -> Any:
    """构建多 Agent 状态图。

    ``invoker`` 是 LLM 调用的注入点。图的其余部分**不碰网络**。
    """

    def fan_out(state: MultiAgentState) -> list[Send]:
        """起点分叉：给每个子 Agent 一个**局部 state**。

        **局部 state 里只有这个 Agent 自己的快照视图**——
        没有其他分支的输出，因此子 Agent **物理上**无法看到彼此。
        这不是靠「约定不传」，而是结构上就没得传。
        """
        sends: list[Send] = []
        for spec in SUB_AGENTS:
            view = agent_snapshot_view(state["snapshot"], spec.snapshot_view)
            sends.append(
                Send(
                    f"agent_{spec.name}",
                    {
                        "snapshot": state["snapshot"],
                        "branch_input": {
                            "agent_name": spec.name,
                            "snapshot_view": view,
                            "snapshot_id": state["snapshot_id"],
                            "as_of": state["as_of"],
                            "time_horizon": state["time_horizon"],
                            "tier": spec.tier,
                        },
                    },
                )
            )
        return sends

    def make_agent_node(agent_name: str) -> GraphNode:
        """为一个子 Agent 生成节点函数。

        节点从 ``branch_input`` 取**自己**的快照视图，
        渲染 Prompt，调用注入的 invoker，
        返回结果（由 reducer 累加到 ``agent_results``）。

        **异常在此被隔离**：一个子 Agent 崩溃（网络异常、代码 bug、
        超时未捕获）**不得**让整次分析失败。
        PRD 24.4 要求「超时则输出『分析超时 + 已完成部分』的部分结果，
        而不是整体失败」——
        若异常穿出去，其他 5 个 Agent 已经算好的结果就**全丢了**，
        而它们是真的花了钱、真的算出来了。

        捕获后转成 ``status=FAILED`` 的结果，
        Master 会把它算进「缺失名单」并**显式标注**（PRD 32.4）——
        既保住了已完成的部分，又没有伪造任何输出。
        """

        def node(state: MultiAgentState) -> dict[str, Any]:
            branch = state["branch_input"]
            spec = _spec_by_name(branch["agent_name"])
            try:
                messages, prompt_version = render_sub_agent_messages(
                    spec,
                    branch["snapshot_view"],
                )
                context = AgentCallContext(
                    snapshot_id=branch["snapshot_id"],
                    as_of=branch["as_of"],
                    time_horizon=branch["time_horizon"],
                    tier=branch["tier"],
                )
                result = invoker(spec, messages, prompt_version, context)
            except Exception as exc:
                logger.exception("Agent %s 执行失败", spec.name)
                result = AgentResult(
                    agent_name=spec.name,
                    status="FAILED",
                    output=None,
                    error={
                        "code": ErrorCode.LLM_UNAVAILABLE,
                        "stage": "agent_execution",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:400],
                    },
                    tier=spec.tier,
                )
            return {"agent_results": [result]}

        return node

    def aggregate(state: MultiAgentState) -> dict[str, Any]:
        """汇聚：检测冲突、算独立来源分组、列出缺失 Agent。"""
        results = list(state.get("agent_results", []))
        conflicts = detect_conflicts(results)
        groups = independent_support_groups(
            [spec for spec in SUB_AGENTS if any(r.agent_name == spec.name for r in results)]
        )
        missing = [r.agent_name for r in results if not r.succeeded]
        return {
            "conflicts": conflicts,
            "evidence_groups": groups,
            "missing_agents": missing,
        }

    def master(state: MultiAgentState) -> dict[str, Any]:
        """Master：综合子 Agent 结论。

        **异常也被隔离**：Master 失败意味着**没有最终结论**，
        但子 Agent 的结果**依然已经算出来了、也依然会被落库**。
        让异常穿出去会让调用方丢掉全部信息，
        而 PRD 32.4 的要求是「显式标注缺失」而不是「让一切归零」。
        """
        results = list(state.get("agent_results", []))
        bundle = EvidenceBundle(
            results=tuple(results),
            conflicts=tuple(state.get("conflicts", [])),
            missing_agents=tuple(state.get("missing_agents", [])),
            evidence_groups=state.get("evidence_groups", {}),
        )
        try:
            messages, prompt_version = render_master_messages(bundle, state["snapshot"])
            context = AgentCallContext(
                snapshot_id=state["snapshot_id"],
                as_of=state["as_of"],
                time_horizon=state["time_horizon"],
                tier=MASTER_SPEC.tier,
            )
            result = invoker(MASTER_SPEC, messages, prompt_version, context)
        except Exception as exc:
            logger.exception("Master Agent 执行失败")
            result = AgentResult(
                agent_name=MASTER_SPEC.name,
                status="FAILED",
                output=None,
                error={
                    "code": ErrorCode.LLM_UNAVAILABLE,
                    "stage": "agent_execution",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:400],
                },
                tier=MASTER_SPEC.tier,
            )
        return {
            "master_result": result,
            "master_output": result.output,
        }

    graph = StateGraph(MultiAgentState)
    for spec in SUB_AGENTS:
        graph.add_node(f"agent_{spec.name}", make_agent_node(spec.name))
    graph.add_node("aggregate", aggregate)
    graph.add_node("master", master)

    graph.add_conditional_edges(START, fan_out, [f"agent_{spec.name}" for spec in SUB_AGENTS])
    for spec in SUB_AGENTS:
        graph.add_edge(f"agent_{spec.name}", "aggregate")
    graph.add_edge("aggregate", "master")
    graph.add_edge("master", END)

    return graph.compile()


def _spec_by_name(name: str) -> AgentSpec:
    from aicrypto.services.agent_registry import AGENTS_BY_NAME

    try:
        return AGENTS_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"未知 Agent：{name!r}") from exc


def run_multi_agent(
    *,
    snapshot: dict[str, Any],
    snapshot_id: str,
    as_of: str,
    time_horizon: str,
    invoker: AgentInvoker,
) -> MultiAgentState:
    """跑一次多 Agent 分析。

    返回最终 state（含 ``agent_results`` / ``master_output`` /
    ``conflicts`` / ``missing_agents``）。
    """
    graph = build_graph(invoker)
    initial: MultiAgentState = {
        "snapshot": snapshot,
        "snapshot_id": snapshot_id,
        "as_of": as_of,
        "time_horizon": time_horizon,
        "agent_results": [],
        "runners": {},
        "conflicts": [],
        "evidence_groups": {},
        "missing_agents": [],
        "master_result": None,
        "master_output": None,
    }
    return graph.invoke(initial)


__all__ = [
    "MASTER_SYSTEM_TEMPLATE",
    "MASTER_USER_TEMPLATE",
    "SUB_SYSTEM_TEMPLATE",
    "AgentCallContext",
    "AgentInvoker",
    "AgentResult",
    "Conflict",
    "EvidenceBundle",
    "MultiAgentState",
    "agent_snapshot_view",
    "build_graph",
    "detect_conflicts",
    "detect_direction",
    "parse_agent_output",
    "render_master_messages",
    "render_sub_agent_messages",
    "run_multi_agent",
]
