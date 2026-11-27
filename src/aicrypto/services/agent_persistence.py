"""多 Agent 结果的持久化（PRD 4.7 / 30.4 / 32.4）。

**这是 ``agent_runs`` / ``agent_outputs`` 两张表的第一个写入方**。
在此之前：
- ``agent_runs`` 只有**一个**写入方（``analysis_worker.record_missing_agent``），
  且**只在失败路径**上写——正常路径一行都没有；
- ``agent_outputs`` **零写入方**——表建了、索引建了，从来没写过。

与 L-30 同族，但更隐蔽：表结构、索引、迁移全都齐备，
从「表存在」这个事实看**一切正常**。

**失败绝不伪造输出**（PRD 32.4）：
失败的 Agent 写 ``agent_runs``（status=FAILED），
**不写** ``agent_outputs``。
理由：一个编出来的 Agent 输出会被当成真实结论参与汇总、
进入报告、进入预测追踪——后面所有环节都**无法再区分它是编的**。
这比「缺一个 Agent」危险得多。
因此本模块的契约是：
**``agent_outputs`` 里的每一行，都对应一个真实成功的模型调用。**

**两阶段写入**（RUNNING -> SUCCESS/FAILED）：
``agent_runs`` 先以 ``RUNNING`` 建立（**flush** 拿到 ``run_id``），
调用结束后再更新为终态。
这样做的关键原因是：``model_runs.agent_run_id`` 是指向
``agent_runs.run_id`` 的外键列（PRD 30.4），且有索引
``idx_model_runs_agent``——而它此前**恒为 NULL**。
若等调用结束才建 ``agent_runs`` 行，计量行就**无处可指**：
计量发生在「收到响应的当下」，而那时 ``agent_runs`` 行还不存在。

**与 L-31 同源的纪律**：这个外键列建了、有索引，
却**从来没有被填过**——从「列存在」看一切正常，
但「某次调用属于哪次 Agent 运行」这个问题**根本答不出来**。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from aicrypto.common.clock import now
from aicrypto.domain.analysis.agent_output import OUTPUT_SCHEMA_VERSION, AgentOutput
from aicrypto.infrastructure.database.models.analysis import AgentOutput as AgentOutputRow
from aicrypto.infrastructure.database.models.analysis import AgentRun
from aicrypto.services.agent_registry import AgentSpec
from aicrypto.services.multi_agent import AgentResult

#: Agent 版本号（PRD 4.7 的 ``agent_version`` 字段）。
#:
#: **刻意与 Prompt 版本分开**：Agent 逻辑变了但 Prompt 没变时，
#: 两者的版本应当**分别**演进。若只有一个版本号，
#: 就无法回答「这次结论变化是 Prompt 变了还是 Agent 逻辑变了」。
AGENT_VERSION = "v1"


def start_agent_run(
    db: Any,
    *,
    spec: AgentSpec,
    analysis_id: str,
    market_snapshot_id: str,
    as_of: dt.datetime,
    time_horizon: str,
    snapshot_view: dict[str, Any],
    prompt_version: str,
    tier: str,
) -> str:
    """建立一行 ``agent_runs``（status=RUNNING），返回 ``run_id``。

    **立即 flush**——``run_id`` 必须立刻可用：
    随后的计量行要用它填 ``model_runs.agent_run_id``。
    """
    run_id = f"arun_{uuid.uuid4().hex}"
    started = now()
    db.add(
        AgentRun(
            run_id=run_id,
            analysis_id=analysis_id,
            agent_name=spec.name,
            agent_version=AGENT_VERSION,
            # **路由完成后才知道真实模型**——此时先留空，
            # 由 ``finish_agent_run`` 用实际结果回填。
            model_id="",
            prompt_version=prompt_version,
            market_snapshot_id=market_snapshot_id,
            as_of=as_of,
            time_horizon=time_horizon,
            status="RUNNING",
            tier=tier,
            degraded=False,
            degraded_reason=None,
            started_at=started,
            finished_at=None,
            duration_ms=None,
            input_ref=_build_input_ref(
                spec=spec,
                market_snapshot_id=market_snapshot_id,
                as_of=as_of,
                time_horizon=time_horizon,
                snapshot_view=snapshot_view,
            ),
            error=None,
        )
    )
    db.flush()
    return run_id


def finish_agent_run(
    db: Any,
    *,
    run_id: str,
    result: AgentResult,
) -> None:
    """把 ``agent_runs`` 行更新为终态；成功时写 ``agent_outputs``。

    **失败不写 ``agent_outputs``**——见模块文档。
    """
    run = db.get(AgentRun, run_id)
    if run is None:
        return
    finished = now()
    run.status = result.status
    run.model_id = result.model_id
    run.prompt_version = result.prompt_version
    run.tier = result.tier or None
    run.degraded = result.degraded
    run.degraded_reason = result.degraded_reason
    run.finished_at = finished
    run.duration_ms = result.duration_ms
    run.error = result.error

    # **失败不写 agent_outputs**——见模块文档。
    if result.status == "SUCCESS" and result.output is not None:
        db.add(
            AgentOutputRow(
                output_id=f"aout_{uuid.uuid4().hex}",
                run_id=run_id,
                output=_serialize_output(result.output),
                schema_version=OUTPUT_SCHEMA_VERSION,
                created_at=finished,
            )
        )


def _build_input_ref(
    *,
    spec: AgentSpec,
    market_snapshot_id: str,
    as_of: dt.datetime,
    time_horizon: str,
    snapshot_view: dict[str, Any],
) -> dict[str, Any]:
    """构造 ``input_ref``——**复现这次判断所需的输入引用**。

    只存**引用**与**视图键**，不存快照全文——
    快照全文已经在 ``market_snapshots`` 里，重复存一份会让表膨胀。
    存「这个 Agent 看到了哪些块」足以复现它的输入裁剪。
    """
    return {
        "agent_name": spec.name,
        "agent_version": AGENT_VERSION,
        "snapshot_view": spec.snapshot_view,
        "snapshot_blocks": sorted(snapshot_view.keys()),
        "market_snapshot_id": market_snapshot_id,
        "as_of": as_of.isoformat(),
        "time_horizon": time_horizon,
        "evidence_domains": list(spec.evidence_domains),
    }


def _serialize_output(output: AgentOutput) -> dict[str, Any]:
    """把 Agent 输出序列化成可写 JSONB 的 dict。

    **时间转 ISO 字符串**——JSONB 不接受 ``datetime``。
    """
    return output.model_dump(mode="json")


__all__ = ["AGENT_VERSION", "finish_agent_run", "start_agent_run"]
