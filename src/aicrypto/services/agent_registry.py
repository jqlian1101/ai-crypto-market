"""多 Agent 定义与职责边界（PRD 4.7 / 4.7.2）。

**这个模块是「谁参与分析」的唯一真源**。
编排（``services/multi_agent.py``）、Prompt 目录、快照视图、
``agent_runs.agent_name`` 的取值**全部从这里派生**——
它们必须一致，否则会出现「Prompt 在 ``prompts/structure/``，
但编排按 ``market-structure`` 找」这种**静默找不到模板**的错。

**为什么子 Agent 必须互相不可见**（PRD 4.7 协作原则 1 / 24.4）：
专业 Agent 独立分析是为了**减少过早互相影响**。
如果 Technical 先看到 Derivatives 的结论，
它就会倾向于「顺着」已形成的解释走——
6 个 Agent 变成 1 个 Agent 调了 6 次，
成本翻 6 倍，换来的是**同一个结论的 6 份拷贝**。
这种「伪多 Agent」比单 Agent **更糟**：它看起来做了独立验证，
实际上没有，而且会**虚增**证据的独立性（见下方「同源指标」纪律）。

**同源指标不得重复计为独立支持**（PRD 4.7.2 末段）：
6 个 Agent 读的是**同一份行情**。
「Technical 说趋势向上」与「Regime 说趋势向上」
若是**同一批 K 线**推出来的，它们**不是两份独立证据**——
统计上它们的相关性接近 1。
Master 必须知道这件事，否则会把「6 个 Agent 都这么说」
误读成「6 个独立信号都指向这里」，从而**系统性高估置信度**。

因此本模块显式声明每个 Agent 的**数据来源域**
（``evidence_domains``）。Master 汇总时据此判断
「这两个支持是否同源」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """一个 Agent 的静态定义。"""

    #: 内部标识（写入 ``agent_runs.agent_name``，也是 Prompt 目录名）
    name: str
    #: 展示名（写进 Prompt 与报告，便于人读）
    display_name: str
    #: 职责边界——**必须回答的独立问题**（PRD 4.7.2 第一列）
    question: str
    #: 职责边界——**不得越界做的事**（PRD 4.7.2 第三列）
    boundary: str
    #: 该 Agent 读取的**数据来源域**，用于判定「同源证据」。
    #: 同一 ``domain`` 内的多个 Agent，其证据**不算独立支持**。
    evidence_domains: tuple[str, ...]
    #: 使用的快照视图键（对应 ``domain/analysis/snapshot.py::AGENT_VIEWS``）
    snapshot_view: str
    #: 建议档位（PRD 24.1：偏确定性的 Agent 可用 LIGHT）
    tier: str

    @property
    def prompt_dir(self) -> str:
        """Prompt 目录名（``prompts/<dir>/``）。"""
        return self.name


#: 6 个专业子 Agent（PRD 4.7 Trading Intelligence Agents，去掉 Master）。
#:
#: 顺序即 fan-out 顺序，**故意按「确定性从高到低」排**：
#: 技术与结构基于同一批 K 线，衍生品与流动性是独立数据源，
#: Regime 是横切背景，Risk 是负向检查。
#: 这个顺序本身没有语义，但**固定下来**能让日志与测试可比对。
SUB_AGENTS: Final[tuple[AgentSpec, ...]] = (
    AgentSpec(
        name="technical",
        display_name="技术面分析",
        question="趋势、动量、波动是否一致，哪里背离？",
        boundary="使用确定性指标，不重复计算，不独占结构判断",
        evidence_domains=("ohlcv",),
        snapshot_view="technical",
        tier="TIER_LIGHT",
    ),
    AgentSpec(
        name="structure",
        display_name="市场结构分析",
        question="关键结构与价位是什么，突破何时成立或失败？",
        boundary="区分已确认结构与待确认假设",
        evidence_domains=("ohlcv",),
        snapshot_view="structure",
        tier="TIER_LIGHT",
    ),
    AgentSpec(
        name="derivatives",
        display_name="衍生品分析",
        question="OI、Funding、Basis 如何印证或反驳价格走势？",
        boundary="不由 OI 单独推断多空方向或持仓主体",
        evidence_domains=("derivatives",),
        snapshot_view="derivatives",
        tier="TIER_LIGHT",
    ),
    AgentSpec(
        name="liquidity",
        display_name="流动性分析",
        question="成交、盘口和深度是否支持当前走势？",
        boundary="标明采样范围，不把挂单推断为确定意图",
        evidence_domains=("liquidity",),
        snapshot_view="liquidity",
        tier="TIER_LIGHT",
    ),
    AgentSpec(
        name="regime",
        display_name="市场状态分析",
        question="当前环境如何影响其他信号的解释？",
        boundary="提供多周期背景，不重复罗列技术指标",
        evidence_domains=("ohlcv",),
        snapshot_view="regime",
        tier="TIER_LIGHT",
    ),
    AgentSpec(
        name="risk",
        display_name="事件与风险分析",
        question="哪些异常、信息缺口或外部因素可能推翻结论？",
        boundary="无新闻/宏观来源时明确「未覆盖」，禁止编造事件归因",
        evidence_domains=("quality",),
        snapshot_view="risk",
        tier="TIER_LIGHT",
    ),
)

#: Master Agent（PRD 4.7.2 最后一行）。
#:
#: **必须综合推理，不能仅拼接子报告或多数投票**（PRD 4.7.2）。
#: 它读**全部**子 Agent 的输出（含分歧），因此它的
#: ``evidence_domains`` 是全集——但这**不代表** Master 的证据与子 Agent 独立，
#: 它是对子 Agent 证据的**综合**，这一点由
#: 「同源指标不得重复计为独立支持」纪律来约束。
MASTER_SPEC: Final[AgentSpec] = AgentSpec(
    name="master",
    display_name="综合研判",
    question="如何权衡证据、处理冲突并形成情景判断？",
    boundary="必须综合推理，不能仅拼接子报告或多数投票",
    evidence_domains=("ohlcv", "derivatives", "liquidity", "quality"),
    snapshot_view="master",
    tier="TIER_STANDARD",
)

#: 全部 Agent（子 Agent + Master），按执行顺序
ALL_AGENTS: Final[tuple[AgentSpec, ...]] = (*SUB_AGENTS, MASTER_SPEC)

#: 按名字索引
AGENTS_BY_NAME: Final[dict[str, AgentSpec]] = {spec.name: spec for spec in ALL_AGENTS}

#: 子 Agent 名字列表
SUB_AGENT_NAMES: Final[tuple[str, ...]] = tuple(spec.name for spec in SUB_AGENTS)

#: 参与统计独立性的**数据来源域**全集
ALL_EVIDENCE_DOMAINS: Final[tuple[str, ...]] = tuple(
    sorted({domain for spec in ALL_AGENTS for domain in spec.evidence_domains})
)


def independent_support_groups(
    specs: list[AgentSpec],
) -> dict[str, list[str]]:
    """把 Agent 按**数据来源域**分组——同组内的支持**不算独立**。

    PRD 4.7.2：「同源指标或同一行情衍生出的相似观点不能重复计为独立支持。
    ……独立调用不代表统计独立，Agent 数量不能作为置信度依据。」

    返回 ``{domain: [agent_name, ...]}``。

    **为什么按域分组而不是按 Agent 计数**：
    「6 个 Agent 里有 3 个说涨」听起来是 3 票，
    但若这 3 个都基于同一批 K 线（``ohlcv`` 域），
    它们**实质上是 1 票**——
    技术指标、结构位、regime 判定本就是同一份 K 线的不同切面。
    真正独立的来源是**数据源**：行情、衍生品、盘口、数据质量。

    因此 Master 判断「支持强度」时，
    应当数的是**独立来源数**，不是 Agent 数。
    """
    groups: dict[str, list[str]] = {}
    for spec in specs:
        for domain in spec.evidence_domains:
            groups.setdefault(domain, []).append(spec.name)
    return groups


def independent_support_count(specs: list[AgentSpec]) -> int:
    """这些 Agent 的**独立证据来源数**。

    同一个 Agent 若读多个域，会在多个域各计一次——
    这**不代表**它有多份独立证据，
    而是「它碰到的数据面更广」。
    保守起见按**去重后的域数**计。
    """
    domains: set[str] = set()
    for spec in specs:
        domains.update(spec.evidence_domains)
    return len(domains)


__all__ = [
    "AGENTS_BY_NAME",
    "ALL_AGENTS",
    "ALL_EVIDENCE_DOMAINS",
    "MASTER_SPEC",
    "SUB_AGENTS",
    "SUB_AGENT_NAMES",
    "AgentSpec",
    "independent_support_count",
    "independent_support_groups",
]
