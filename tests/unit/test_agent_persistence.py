"""``agent_runs`` / ``agent_outputs`` 落库的测试（PRD 4.7 / 32.4）。

**背景**��``agent_runs`` / ``agent_outputs`` 两张表早已存在，
但 ``agent_outputs`` **零写入方**——表建了、索引建了，从来没写过。
与 L-30 同族：从「表存在」这个事实看一切正常。

**这些测试锁死两条红线**：

1. **``agent_outputs`` 里的每一行，都必须对应一个真实成功的模型调用**（PRD 32.4）
   ——失败绝不能写；
2. **两阶段写入**：``agent_runs`` 先以 ``RUNNING`` 建立并 **flush**
   （拿到 ``run_id``），再更新为终态。
   理由：``model_runs.agent_run_id`` 是指向 ``agent_runs.run_id`` 的外键列，
   而计量发生在「收到响应的当下」——
   若 ``agent_runs`` 行到调用结束才建，计量行就**无处可指**。

**用真实的 ORM 对象但不连数据库**：
``start_agent_run`` / ``finish_agent_run`` 只做 ``db.add(...)`` / ``db.get(...)``，
因此用假 session 即可断言内容——不需要真库，且断言更精确。
真库的写入路径由集成测试覆盖。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from aicrypto.domain.analysis.agent_output import AgentOutput
from aicrypto.infrastructure.database.models.analysis import AgentOutput as AgentOutputRow
from aicrypto.infrastructure.database.models.analysis import AgentRun
from aicrypto.services.agent_persistence import (
    AGENT_VERSION,
    finish_agent_run,
    start_agent_run,
)
from aicrypto.services.agent_registry import AGENTS_BY_NAME
from aicrypto.services.multi_agent import AgentResult

AS_OF = dt.datetime(2026, 11, 27, 0, 0, tzinfo=dt.UTC)


class _FakeSession:
    """收集 ``add`` 过的对象，并支持 ``get`` / ``flush``。"""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flushed = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flushed += 1

    def get(self, cls: type, pk: Any) -> Any:
        for obj in self.added:
            if isinstance(obj, cls) and obj.run_id == pk:
                return obj
        return None

    def of_type(self, cls: type) -> list[Any]:
        return [obj for obj in self.added if isinstance(obj, cls)]


def _output(agent_name: str = "technical") -> AgentOutput:
    return AgentOutput(
        agent_name=agent_name,
        agent_version=AGENT_VERSION,
        model_id="test-model",
        prompt_version="v1",
        market_snapshot_id="snap_test",
        as_of=AS_OF,
        time_horizon="4h",
        summary="技术面偏多",
        findings=["一条发现"],
        confidence=0.6,
    )


def _success(agent_name: str = "technical") -> AgentResult:
    return AgentResult(
        agent_name=agent_name,
        status="SUCCESS",
        output=_output(agent_name),
        duration_ms=1234,
        model_id="test-model",
        prompt_version="v1",
        tier="TIER_LIGHT",
    )


def _failure(agent_name: str = "technical") -> AgentResult:
    return AgentResult(
        agent_name=agent_name,
        status="FAILED",
        output=None,
        error={"code": "LLM_UNAVAILABLE", "stage": "transport"},
        duration_ms=500,
        model_id="test-model",
        prompt_version="v1",
        tier="TIER_LIGHT",
        degraded=True,
        degraded_reason="LOCAL_UNAVAILABLE",
    )


def _start(db: _FakeSession, agent_name: str = "technical") -> str:
    return start_agent_run(
        db,
        spec=AGENTS_BY_NAME[agent_name],
        analysis_id="ana_test",
        market_snapshot_id="snap_test",
        as_of=AS_OF,
        time_horizon="4h",
        snapshot_view={"current_price": {}, "indicators": {}},
        prompt_version="v1",
        tier="TIER_LIGHT",
    )


# --------------------------------------------------------------------------
# 两阶段写入
# --------------------------------------------------------------------------


def test_start_creates_running_row_and_flushes() -> None:
    """``start_agent_run`` 必须**立即 flush**。

    ``run_id`` 随后要被 ``model_runs.agent_run_id`` 引用，
    若不 flush 就拿不到——而计量发生在「收到响应的当下」，
    那时 Agent 还没结束。
    """
    db = _FakeSession()
    run_id = _start(db)

    runs = db.of_type(AgentRun)
    assert len(runs) == 1
    assert runs[0].run_id == run_id
    assert runs[0].status == "RUNNING"
    assert db.flushed == 1, "必须 flush，否则 run_id 无法被引用"


def test_finish_updates_status_to_success() -> None:
    db = _FakeSession()
    run_id = _start(db)
    finish_agent_run(db, run_id=run_id, result=_success())

    run = db.of_type(AgentRun)[0]
    assert run.status == "SUCCESS"
    assert run.model_id == "test-model"
    assert run.duration_ms == 1234
    assert run.finished_at is not None


def test_finish_writes_agent_output_on_success() -> None:
    db = _FakeSession()
    run_id = _start(db)
    finish_agent_run(db, run_id=run_id, result=_success())

    outputs = db.of_type(AgentOutputRow)
    assert len(outputs) == 1
    assert outputs[0].run_id == run_id


def test_agent_run_carries_provenance() -> None:
    """溯源字段一个都不能少（PRD 4.7 协作原则 6「便于复现」）。"""
    db = _FakeSession()
    _start(db)
    run = db.of_type(AgentRun)[0]

    assert run.agent_name == "technical"
    assert run.agent_version == AGENT_VERSION
    assert run.prompt_version == "v1"
    assert run.market_snapshot_id == "snap_test"
    assert run.tier == "TIER_LIGHT"
    assert run.as_of == AS_OF
    assert run.time_horizon == "4h"


def test_input_ref_records_what_agent_saw() -> None:
    """``input_ref`` 记录该 Agent 看到的**快照视图**。

    只存引用与视图键，不存快照全文——
    全文已经在 ``market_snapshots`` 里，重复存会让表膨胀。
    """
    db = _FakeSession()
    _start(db)
    run = db.of_type(AgentRun)[0]

    assert run.input_ref["snapshot_view"] == "technical"
    assert run.input_ref["snapshot_blocks"] == ["current_price", "indicators"]
    assert run.input_ref["market_snapshot_id"] == "snap_test"
    assert run.input_ref["as_of"] == AS_OF.isoformat()
    assert run.input_ref["evidence_domains"] == ["ohlcv"]


def test_agent_output_is_json_serializable() -> None:
    """``output`` 必须能写进 JSONB——时间不能是 ``datetime`` 对象。"""
    db = _FakeSession()
    run_id = _start(db)
    finish_agent_run(db, run_id=run_id, result=_success())
    output = db.of_type(AgentOutputRow)[0]

    assert output.schema_version == "1"
    assert isinstance(output.output, dict)
    assert output.output["agent_name"] == "technical"
    assert isinstance(output.output["as_of"], str)


# --------------------------------------------------------------------------
# 失败路径——**绝不伪造输出**（PRD 32.4）
# --------------------------------------------------------------------------


def test_failure_updates_run_but_writes_no_agent_output() -> None:
    """失败：**有** ``agent_runs``（终态 FAILED），**没有** ``agent_outputs``。

    这是本模块最重要的一条契约。
    """
    db = _FakeSession()
    run_id = _start(db)
    finish_agent_run(db, run_id=run_id, result=_failure())

    runs = db.of_type(AgentRun)
    outputs = db.of_type(AgentOutputRow)

    assert len(runs) == 1, "失败也必须留痕"
    assert runs[0].status == "FAILED"
    assert outputs == [], "失败绝不能写 agent_outputs——那是伪造输出"


def test_failure_run_records_error_and_degradation() -> None:
    db = _FakeSession()
    run_id = _start(db)
    finish_agent_run(db, run_id=run_id, result=_failure())
    run = db.of_type(AgentRun)[0]

    assert run.degraded is True
    assert run.degraded_reason == "LOCAL_UNAVAILABLE"
    assert run.error is not None
    assert run.error["code"] == "LLM_UNAVAILABLE"


@pytest.mark.parametrize(
    ("status", "expect_output"),
    [
        ("SUCCESS", True),
        ("FAILED", False),
        ("TIMEOUT", False),
        ("SKIPPED_DEGRADED", False),
    ],
)
def test_only_success_writes_agent_output(status: str, expect_output: bool) -> None:
    """只有 ``SUCCESS`` 才写 ``agent_outputs``。

    其余状态（FAILED / TIMEOUT / SKIPPED_DEGRADED）
    都表示**没有可信输出**，写进去就是伪造。

    **所有分支都带上一个非空的 ``output`` 对象**——
    否则「没写 output 行」可能只是因为「没有 output 可写」，
    而不是「状态把关拦住了它」。
    那样的话，实现只要把判断改成「有 output 就写」，
    这个用例**照样通过**——它就锁不住任何东西。
    真正的把关点是**状态**，所以测试必须让
    「output 存在」与「状态非 SUCCESS」同时成立。
    """
    db = _FakeSession()
    run_id = _start(db)
    result = AgentResult(
        agent_name="technical",
        status=status,
        output=_output(),  # **所有状态都带 output**
        model_id="test-model",
        prompt_version="v1",
        tier="TIER_LIGHT",
    )
    finish_agent_run(db, run_id=run_id, result=result)

    outputs = db.of_type(AgentOutputRow)
    assert bool(outputs) is expect_output


def test_failed_result_with_output_object_still_writes_no_output_row() -> None:
    """即使 ``output`` 字段非空，只要 status 不是 SUCCESS 也不写。

    防御性：防止上游把「失败」误标成别的状态时，
    一个来路不明的输出被当成真实结论。
    """
    db = _FakeSession()
    run_id = _start(db)
    result = AgentResult(
        agent_name="technical",
        status="TIMEOUT",
        output=_output(),  # 状态说失败
        model_id="test-model",
        prompt_version="v1",
        tier="TIER_LIGHT",
    )
    finish_agent_run(db, run_id=run_id, result=result)
    assert db.of_type(AgentOutputRow) == []
