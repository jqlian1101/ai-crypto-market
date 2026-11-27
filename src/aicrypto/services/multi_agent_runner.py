"""多 Agent 分析的执行入口（PRD 4.7 / 24.4）。

**这个模块负责把「图」「模型调用」「持久化」串起来**：
``multi_agent`` 提供拓扑与纪律，
``llm_agent`` 负责发请求与计量，
``agent_persistence`` 负责落库，
本模块是它们的粘合层。

**与单 Agent 路径并存，不替换**：
``analysis_worker.process_one`` 走的是单 Agent 路径（PRD 4.7.3 明确要求
**配对比较**「规则基线、单模型综合分析和多 Agent 分析」——
单 Agent 路径正是对照组，**不能删**）。
本模块是多 Agent 路径。

**线程安全：DB 写入必须串行化**（**这是本模块最容易踩的坑**）。

LangGraph 的 fan-out 是**真并行**——6 个分支跑在 6 个不同的
``ThreadPoolExecutor`` 线程上。而 SQLAlchemy 的 ``Session``
**不是线程安全的**：并发在同一个 Session 上 ``add()`` / ``flush()``
会让 flush 交错，导致重复主键、事务错乱、甚至丢行。

**实测症状**：6 个分支各自 ``db.add(ModelRun)`` + ``db.commit()``，
autoflush 在 flush 过程中触发另一个分支的 ``add()``，
报 ``duplicate key value violates unique constraint "pk_llm_cost_records"``。

因此本模块的纪律是：
**分支线程里只做「网络调用 + 纯计算」，绝不碰 Session**。
所有 DB 写入（``agent_runs`` / ``agent_outputs`` / 计量行）
都在**主线程**里、**按顺序**做。

代价是「Agent 调用进行中」这段时间数据库里看不到对应的
``agent_runs`` 行——这是**可以接受的**：
PRD 24.4 要求的是「每次 Agent 调用记录输入快照引用、输出、耗时、token，
形成完整调用链」，而不是「调用开始瞬间就要有行」。
调用**结束**后所有信息都完整落库，且能对上 ``model_runs.agent_run_id``。
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, field
from typing import Any

from aicrypto.common.clock import now
from aicrypto.config.settings import Settings
from aicrypto.domain.analysis.agent_output import AgentOutput
from aicrypto.services.agent_persistence import finish_agent_run, start_agent_run
from aicrypto.services.agent_registry import AGENTS_BY_NAME, MASTER_SPEC
from aicrypto.services.llm_agent import call_agent
from aicrypto.services.multi_agent import (
    AgentCallContext,
    AgentResult,
    Conflict,
    agent_snapshot_view,
    run_multi_agent,
)

#: 默认时间跨度
DEFAULT_TIME_HORIZON = "4h"


@dataclass(slots=True)
class MultiAgentOutcome:
    """一次多 Agent 分析的结果。"""

    #: 子 Agent 的结果（6 个，无论成败）
    agent_results: list[AgentResult] = field(default_factory=list)
    #: Master 的结果
    master_result: AgentResult | None = None
    #: 检测到的冲突
    conflicts: list[Conflict] = field(default_factory=list)
    #: 失败的 Agent 名字
    missing_agents: list[str] = field(default_factory=list)
    #: 写入的 ``agent_runs.run_id`` 列表
    agent_run_ids: list[str] = field(default_factory=list)
    #: 是否超时
    timed_out: bool = False

    @property
    def master_output(self) -> AgentOutput | None:
        return self.master_result.output if self.master_result else None

    @property
    def succeeded(self) -> bool:
        """整体是否算成功。

        **Master 失败才算整体失败**。
        子 Agent 缺失**不**算失败——PRD 32.4 要求
        「显式标注该 Agent 缺失」，而不是让整次分析归零。
        """
        return self.master_result is not None and self.master_result.succeeded


def run_multi_agent_analysis(
    *,
    snapshot: dict[str, Any],
    settings: Settings,
    db: Any,
    analysis_id: str,
    meter_db: Any,
    record_attempt: Any,
    time_horizon: str = DEFAULT_TIME_HORIZON,
    commit_metering: bool = True,
) -> MultiAgentOutcome:
    """跑一次完整的多 Agent 分析并落库。

    ``db`` 是**报告事务**（写 ``agent_runs`` / ``agent_outputs``），
    ``meter_db`` 是**独立的事务**（写计量行）——
    二者分开是 L-24 的要求：报告回滚不应让计量一起消失。

    **DB 写入全部在主线程串行完成**——见模块文档的线程安全说明。
    """
    snapshot_id = str(snapshot.get("snapshot_id", ""))
    as_of = _parse_as_of(snapshot.get("as_of"))
    deadline = now() + dt.timedelta(seconds=settings.LLM_ANALYSIS_TOTAL_BUDGET_SECONDS)

    #: 分支线程里收集到的计量记录——**不在分支线程里写库**。
    pending_metering: list[dict[str, Any]] = []
    pending_lock = threading.Lock()

    def deferred_metering(db: Any, **kwargs: Any) -> None:
        """分支线程里的计量回调——**只收集，不写库**。

        ``db`` 被忽略（分支线程没有安全的 Session）。
        写库由主线程在图跑完后**按顺序**完成。
        """
        with pending_lock:
            pending_metering.append(kwargs)

    def invoker(
        spec: Any,
        messages: list[dict[str, str]],
        prompt_version: str,
        context: AgentCallContext,
    ) -> AgentResult:
        """把图的调用请求接到真实模型上。

        **分支线程里不做任何 DB 写入**——只发请求、解析输出。

        ``prompt_version`` 由**渲染时**确定并传入——
        调用方若自己去重新读文件，会引入
        「渲染用的版本」与「记录用的版本」不一致的可能。
        """
        return call_agent(
            spec=spec,
            messages=messages,
            prompt_version=prompt_version,
            context=context,
            settings=settings,
            db=None,  # **分支线程不碰 Session**
            record_attempt=deferred_metering,
            commit_metering=False,
        )

    state = run_multi_agent(
        snapshot=snapshot,
        snapshot_id=snapshot_id,
        as_of=as_of.isoformat(),
        time_horizon=time_horizon,
        invoker=invoker,
    )

    agent_results: list[AgentResult] = list(state.get("agent_results", []))
    master_result: AgentResult | None = state.get("master_result")

    # ---- 落库（主线程，串行）----
    agent_run_ids: list[str] = []
    pending: list[tuple[str, AgentResult]] = []

    for result in agent_results:
        spec = AGENTS_BY_NAME.get(result.agent_name)
        if spec is None:
            continue
        view = agent_snapshot_view(snapshot, spec.snapshot_view)
        run_id = start_agent_run(
            db,
            spec=spec,
            analysis_id=analysis_id,
            market_snapshot_id=snapshot_id,
            as_of=as_of,
            time_horizon=time_horizon,
            snapshot_view=view,
            prompt_version=result.prompt_version,
            tier=result.tier,
        )
        agent_run_ids.append(run_id)
        pending.append((run_id, result))

    if master_result is not None:
        master_view = agent_snapshot_view(snapshot, MASTER_SPEC.snapshot_view)
        run_id = start_agent_run(
            db,
            spec=MASTER_SPEC,
            analysis_id=analysis_id,
            market_snapshot_id=snapshot_id,
            as_of=as_of,
            time_horizon=time_horizon,
            snapshot_view=master_view,
            prompt_version=master_result.prompt_version,
            tier=master_result.tier,
        )
        agent_run_ids.append(run_id)
        pending.append((run_id, master_result))

    for run_id, result in pending:
        finish_agent_run(db, run_id=run_id, result=result)

    db.flush()

    # ---- 计量（主线程，串行）----
    # L-24：计量与报告分属两个事务，报告回滚不应让计量一起消失。
    for kwargs in pending_metering:
        record_attempt(meter_db, **kwargs)

    return MultiAgentOutcome(
        agent_results=agent_results,
        master_result=master_result,
        conflicts=list(state.get("conflicts", [])),
        missing_agents=list(state.get("missing_agents", [])),
        agent_run_ids=agent_run_ids,
        timed_out=now() > deadline,
    )


def _parse_as_of(value: Any) -> dt.datetime:
    """解析快照的 ``as_of``。"""
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return now()


__all__ = ["DEFAULT_TIME_HORIZON", "MultiAgentOutcome", "run_multi_agent_analysis"]
