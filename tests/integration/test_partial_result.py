"""部分结果与计量存活的真库验证（PRD 24.4 / L-24）。

**两个必须真库才能验的不变量**：

1. **超时/失败时输出「已完成部分」而非整体失败**（PRD 24.4）。
   这需要完整的 ``process_one`` 流程：建快照 -> 调模型 -> 失败
   -> 发部分报告 -> 落终态。全流程串起来才能验。

2. **计量记录不被事务回滚销毁**（L-24）。
   这需要真实的异常传播路径——单元测试传一个 session 进去、
   在事务外捕获异常，**看不到回滚**。只有让异常穿过
   ``session_scope`` 才能暴露。

**为什么第 2 条尤其重要**：失败尝试烧掉的 token 与费用是真的花了。
把它们从账本里抹掉，成本报表就**系统性低估**——
而且是「越是在模型不稳定的时候低估得越多」，
恰好是你最需要看清成本的时候。
"""

from __future__ import annotations

import datetime as dt
import json
import os
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aicrypto.common.errors import AICryptoError, ErrorCode
from aicrypto.config.settings import Settings
from aicrypto.infrastructure.database.models.analysis import (
    AgentOutput,
    AgentRun,
    LLMCostRecord,
    MarketSnapshotRecord,
    ModelPricing,
    ModelRun,
)
from aicrypto.infrastructure.database.models.report import (
    AnalysisReportRecord,
    AnalysisRunRecord,
)
from aicrypto.services import analysis_worker
from aicrypto.services.partial_result import PARTIAL_BIAS

pytestmark = pytest.mark.integration

DB_URL = "postgresql+psycopg://aicrypto:aicrypto@127.0.0.1:5432/aicrypto"


@pytest.fixture
def db_env():
    url = os.getenv("ANALYSIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 ANALYSIS_TEST_DATABASE_URL 以执行 PostgreSQL 集成测试")
    engine = create_engine(url)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        engine.dispose()


@pytest.fixture
def shared_scope(db_env: Session):
    """把 ``analysis_worker.session_scope`` 换成共享测试事务的版本。

    **必须这样做**：``process_one`` 自己开事务（``session_scope``），
    而测试的 ``db_env`` 在**另一个事务**里。未提交的种子数据
    对 ``process_one`` 的连接不可见——它会认为「没有待处理的任务」
    而直接返回 ``False``。

    共享事务后，``process_one`` 的写与测试的断言看见的是同一份数据，
    测试结束后整体回滚，不留垃圾。
    """
    from contextlib import contextmanager

    @contextmanager
    def scope(_settings):
        yield db_env
        db_env.flush()

    original = analysis_worker.session_scope
    analysis_worker.session_scope = scope  # type: ignore[assignment]
    try:
        yield db_env
    finally:
        analysis_worker.session_scope = original  # type: ignore[assignment]


def _seed_run(db: Session, run_id: str, symbol: str = "PARTIALUSDT") -> str:
    """插入一条 **QUEUED** 的分析任务。

    必须是 ``QUEUED`` 而不是 ``RUNNING``：``claim_run`` 只挑
    ``QUEUED`` 的任务（``RUNNING`` 的会被当成超时任务回收）。
    """
    db.add(
        AnalysisRunRecord(
            run_id=run_id,
            analysis_id=f"analysis_{run_id}",
            symbol=symbol,
            market_type="SPOT",
            trigger="MANUAL",
            status="QUEUED",
            analysis_mode="LIVE",
            created_at=dt.datetime.now(dt.UTC),
            started_at=dt.datetime.now(dt.UTC),
        )
    )
    db.commit()
    return run_id


def _seed_pricing(db: Session, model_id: str) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    db.execute(
        pg_insert(ModelPricing)
        .values(
            model_id=model_id,
            pricing_version="v1",
            provider="cloud",
            input_usd_per_mtok=Decimal("3.0"),
            output_usd_per_mtok=Decimal("15.0"),
            currency="USD",
            effective_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
            created_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        )
        .on_conflict_do_nothing(index_elements=["model_id", "pricing_version"])
    )
    db.commit()


def _install_failing_client(status: int = 503):
    """让所有 HTTP 调用返回指定状态码。"""
    original = httpx.Client

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(status, json={"error": "boom"}, request=request)
        )
        return original(*args, **kwargs)

    httpx.Client = patched  # type: ignore[assignment]
    return original


@pytest.fixture(autouse=True)
def cleanup_test_rows():
    """测试结束后清掉本文件造出来的行。

    **必须在断言失败时也执行**——否则失败的测试会把垃圾留在库里，
    下次跑同一批测试时计数对不上（``model_runs`` 累积到 8 行之类），
    排查方向会被带偏。

    这些行是**已提交**的（测试共享事务后 ``commit`` 过），
    所以外层事务的回滚清不掉它们，必须显式删。
    """
    engine = create_engine(DB_URL)
    yield
    models = [
        "rollback-metering-model",
        "partial-failure-model",
        "partial-schema-model",
        "partial-timeout-model",
    ]
    with Session(engine) as db:
        db.execute(LLMCostRecord.__table__.delete().where(LLMCostRecord.model_id.in_(models)))
        db.execute(ModelRun.__table__.delete().where(ModelRun.model_id.in_(models)))
        db.execute(ModelPricing.__table__.delete().where(ModelPricing.model_id.in_(models)))
        db.execute(
            AgentOutput.__table__.delete().where(
                AgentOutput.run_id.in_(
                    [
                        "arun_partial_failure",
                        "arun_partial_schema",
                        "arun_partial_timeout",
                    ]
                )
            )
        )
        db.execute(
            AgentRun.__table__.delete().where(
                AgentRun.analysis_id.in_(
                    [
                        "analysis_run_partial_failure",
                        "analysis_run_partial_schema",
                        "analysis_run_partial_timeout",
                    ]
                )
            )
        )
        db.execute(
            AnalysisReportRecord.__table__.delete().where(
                AnalysisReportRecord.analysis_id.in_(
                    [
                        "analysis_run_partial_failure",
                        "analysis_run_partial_schema",
                        "analysis_run_partial_timeout",
                    ]
                )
            )
        )
        db.execute(
            AnalysisRunRecord.__table__.delete().where(
                AnalysisRunRecord.run_id.in_(
                    [
                        "run_partial_failure",
                        "run_partial_schema",
                        "run_partial_timeout",
                    ]
                )
            )
        )
        db.execute(
            MarketSnapshotRecord.__table__.delete().where(
                MarketSnapshotRecord.snapshot_id.in_(
                    [
                        "snap_run_partial_failure",
                        "snap_run_partial_schema",
                        "snap_run_partial_timeout",
                    ]
                )
            )
        )
        db.commit()
    engine.dispose()


def _fake_snapshot_builder(db: Session):
    """替换 ``build_snapshot``：造一份**已落库**的快照。

    真实 ``build_snapshot`` 需要库里有 K 线数据；这里只关心
    「快照已就绪 + 快照里有确定性事实」这个前提。

    **必须真的落库**——``create_report`` 会把证据的 ``source_path``
    对着**库里的**快照校验（路径解析不了会抛 ``LLM_SCHEMA_INVALID``，
    值不一致会抛 ``EVAL_LOOKAHEAD_VIOLATION``）。
    只在内存里造一个 dict 是不够的。
    """
    from aicrypto.infrastructure.database.models.analysis import MarketSnapshotRecord

    def _build(run_id: str, settings: Settings) -> dict:
        snapshot_id = f"snap_{run_id}"
        content = {
            "indicators": {
                "5m": {
                    "bars_used": 300,
                    "rsi_14": 55.0,
                    "sr_zones": [{"low": 100.0, "high": 102.0, "touches": 3, "kind": "support"}],
                },
                "1h": {"bars_used": 300, "sr_zones": []},
            },
            "market_structure": {"5m": {"bias": "UP"}},
            "market_regime": {"5m": {"state": "UPTREND"}, "1h": {"state": "RANGE"}},
        }
        moment = dt.datetime.now(dt.UTC)
        db.add(
            MarketSnapshotRecord(
                snapshot_id=snapshot_id,
                symbol="PARTIALUSDT",
                market_type="SPOT",
                as_of=moment,
                available_as_of=moment,
                created_at=moment,
                data_quality="GOOD",
                data_quality_detail={},
                feature_version="v1",
                schema_version="2",
                content=content,
                source_watermarks={},
            )
        )
        db.flush()
        return {
            "snapshot_id": snapshot_id,
            "symbol": "PARTIALUSDT",
            "as_of": moment.isoformat(),
            "available_as_of": moment.isoformat(),
            **content,
        }

    return _build


def test_metering_survives_transaction_rollback(db_env: Session) -> None:
    """**回归 L-24**：异常穿过事务后，计量记录必须还在。

    曾经的缺陷：``process_one`` 把「写计量」与「写报告」放在
    **同一个事务**里。模型调用失败时异常穿出 ``session_scope``，
    整个事务回滚——**同事务里的 ``model_runs`` / ``llm_cost_records``
    一起消失**。失败尝试烧掉的 token 与费用从来没进过账本，
    成本报表因此**系统性低估**。

    单元测试抓不到这个：它们传一个 session 进去、在**事务外**
    捕获异常，session 没被回滚，所以记录还在。
    """
    model_id = "rollback-metering-model"
    settings = Settings(
        _env_file=None,
        DATABASE_URL=DB_URL,
        REDIS_URL="redis://127.0.0.1:6379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL="https://example.invalid/v1",
        LLM_CLOUD_API_KEY="k",
        LLM_TIER_STANDARD_MODEL=model_id,
        LLM_MAX_RETRIES=1,
        LLM_SCHEMA_REPAIR_ATTEMPTS=0,
    )
    _seed_pricing(db_env, model_id)

    original = _install_failing_client(503)
    try:
        # **关键**：异常必须穿过 session_scope —— 只有这样才会触发回滚
        from aicrypto.infrastructure.database.session import session_scope

        with pytest.raises(AICryptoError) as excinfo, session_scope(settings) as db:
            analysis_worker.generate_report(
                {"indicators": {}},
                settings,
                db=db,
                commit_metering=True,
            )
        assert excinfo.value.code == ErrorCode.LLM_UNAVAILABLE
    finally:
        httpx.Client = original  # type: ignore[assignment]

    runs = list(db_env.scalars(select(ModelRun).where(ModelRun.model_id == model_id)))
    costs = list(db_env.scalars(select(LLMCostRecord).where(LLMCostRecord.model_id == model_id)))

    assert len(runs) == 2, f"2 次尝试（1 次 + 1 次重试）的计量必须存活，实际 {len(runs)} 行"
    assert len(costs) == 2, f"2 次尝试的成本记录必须存活，实际 {len(costs)} 行"
    assert all(r.status == "FAILED" for r in runs)
    # 两表一一对应，无孤儿
    assert {c.model_run_id for c in costs} == {r.model_run_id for r in runs}


def test_partial_result_published_on_model_failure(shared_scope: Session) -> None:
    """模型失败时输出「已完成部分」的部分报告，而不是整体失败（PRD 24.4）。

    场景是「快照已建好、模型调用失败」——
    快照里的确定性计算结果**已经算完了**，那就是「已完成的部分」。
    """
    db_env = shared_scope
    model_id = "partial-failure-model"
    run_id = "run_partial_failure"
    settings = Settings(
        _env_file=None,
        DATABASE_URL=DB_URL,
        REDIS_URL="redis://127.0.0.1:6379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL="https://example.invalid/v1",
        LLM_CLOUD_API_KEY="k",
        LLM_TIER_STANDARD_MODEL=model_id,
        LLM_MAX_RETRIES=0,
        LLM_SCHEMA_REPAIR_ATTEMPTS=0,
    )
    _seed_pricing(db_env, model_id)
    _seed_run(db_env, run_id)

    original = _install_failing_client(503)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(analysis_worker, "build_snapshot", _fake_snapshot_builder(db_env))
            assert analysis_worker.process_one(settings)
    finally:
        httpx.Client = original  # type: ignore[assignment]

    db_env.expire_all()
    report = db_env.get(AnalysisReportRecord, f"analysis_{run_id}")
    run = db_env.get(AnalysisRunRecord, run_id)

    assert report is not None, "**必须产出部分报告**，而不是什么都不产出"
    assert run is not None
    assert run.status == "PARTIAL", f"运行状态应为 PARTIAL，实际 {run.status}"

    # ---- 报告必须明确标出降级 ----
    assert report.degraded is True
    assert report.degraded_reason is not None
    assert report.degraded_reason in {"TIMEOUT", "MODEL_CALL_FAILED"}

    content = report.content
    assert content["market_bias"] == PARTIAL_BIAS, "**不得给出方向判断**"
    assert content["scenarios"] == []
    assert content["scenario_probabilities"] is None
    assert content["disagreements"] == []
    assert "未完成" in content["summary"]
    assert content["uncertainties"], "必须说明缺了什么"


def test_schema_failure_does_not_publish_partial_result(shared_scope: Session) -> None:
    """**Schema 修复失败不产出部分报告**——那会把不可信内容洗成「部分可信」。

    区别在于：Schema 失败时模型**跑完了**，只是输出不合法。
    它的「已完成部分」不是快照里的确定性事实，
    而是模型给出（但不合规）的输出——拿它拼一份部分报告，
    等于把不可信内容包装成「部分可信的结论」。
    """
    db_env = shared_scope
    model_id = "partial-schema-model"
    run_id = "run_partial_schema"
    settings = Settings(
        _env_file=None,
        DATABASE_URL=DB_URL,
        REDIS_URL="redis://127.0.0.1:6379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL="https://example.invalid/v1",
        LLM_CLOUD_API_KEY="k",
        LLM_TIER_STANDARD_MODEL=model_id,
        LLM_MAX_RETRIES=0,
        LLM_SCHEMA_REPAIR_ATTEMPTS=1,
    )
    _seed_pricing(db_env, model_id)
    _seed_run(db_env, run_id)

    bad = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps({"summary": "只有摘要"})},
            }
        ],
        "usage": {"prompt_tokens": 800, "completion_tokens": 50},
    }

    original = httpx.Client

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(200, json=bad, request=request)
        )
        return original(*args, **kwargs)

    httpx.Client = patched  # type: ignore[assignment]
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(analysis_worker, "build_snapshot", _fake_snapshot_builder(db_env))
            assert analysis_worker.process_one(settings)
    finally:
        httpx.Client = original  # type: ignore[assignment]

    db_env.expire_all()
    report = db_env.get(AnalysisReportRecord, f"analysis_{run_id}")
    run = db_env.get(AnalysisRunRecord, run_id)

    assert report is None, "Schema 失败**不得**产出部分报告"
    assert run is not None
    assert run.status == "FAILED", f"运行状态应为 FAILED，实际 {run.status}"

    # 必须如实登记「Agent 缺失」（PRD 32.4），不伪造输出
    agent_runs = list(
        db_env.scalars(select(AgentRun).where(AgentRun.analysis_id == f"analysis_{run_id}"))
    )
    assert agent_runs, "必须登记 Agent 缺失"
    assert all(r.status == "FAILED" for r in agent_runs)
    assert all(r.degraded is True for r in agent_runs)
    agent_outputs = list(
        db_env.scalars(select(AgentOutput).where(AgentOutput.run_id == agent_runs[0].run_id))
    )
    assert not agent_outputs, "**不得伪造 Agent 输出**"


def test_timeout_is_classified_as_timeout_not_call_failure(
    shared_scope: Session,
) -> None:
    """**回归**：超时必须被标成 ``TIMEOUT``，不能标成 ``MODEL_CALL_FAILED``。

    曾经的缺陷：``generate_report`` 把 ``httpx.ReadTimeout`` 翻译成
    ``AICryptoError(LLM_TIMEOUT)``，而 ``process_one`` 只按**异常类型**
    判定超时（``isinstance(exc, httpx.TimeoutException)``）——
    包过一层之后类型检查失配，**超时被当成普通调用失败**。

    后果：``degraded_reason`` 写的是 ``MODEL_CALL_FAILED``，
    而 ``error_code`` 是 ``LLM_TIMEOUT``——**降级原因与真实原因不符**。
    运维按降级原因排查会去查「模型为什么报错」，
    而真实原因是「上游卡住没响应」——**查错方向，浪费排查时间**。
    """
    db_env = shared_scope
    model_id = "partial-timeout-model"
    run_id = "run_partial_timeout"
    settings = Settings(
        _env_file=None,
        DATABASE_URL=DB_URL,
        REDIS_URL="redis://127.0.0.1:6379/0",
        AUTH_ENABLED=False,
        LLM_CLOUD_BASE_URL="https://example.invalid/v1",
        LLM_CLOUD_API_KEY="k",
        LLM_TIER_STANDARD_MODEL=model_id,
        LLM_MAX_RETRIES=0,
        LLM_SCHEMA_REPAIR_ATTEMPTS=0,
    )
    _seed_pricing(db_env, model_id)
    _seed_run(db_env, run_id)

    original = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        # **必须是真正的超时异常**，而不是 504 这类 HTTP 层错误。
        # 504 是「服务器回了一个状态码」，那是网关错误；
        # 超时是「请求发出去了，服务器一声不吭」——
        # 两者在代码里走完全不同的分支，判定结果也不同。
        raise httpx.ReadTimeout("read timeout", request=request)

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    httpx.Client = patched  # type: ignore[assignment]
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(analysis_worker, "build_snapshot", _fake_snapshot_builder(db_env))
            assert analysis_worker.process_one(settings)
    finally:
        httpx.Client = original  # type: ignore[assignment]

    db_env.expire_all()
    report = db_env.get(AnalysisReportRecord, f"analysis_{run_id}")
    run = db_env.get(AnalysisRunRecord, run_id)

    assert report is not None, "超时应产出部分报告"
    assert run is not None
    assert run.status == "PARTIAL", f"运行状态应为 PARTIAL，实际 {run.status}"
    # **关键断言**：降级原因必须如实反映「超时」
    assert report.degraded_reason == "TIMEOUT", (
        f"超时的降级原因必须是 TIMEOUT，实际 {report.degraded_reason!r}"
    )
    # 超时的部分结果同样不得伪造结论
    assert report.content["market_bias"] == PARTIAL_BIAS
    assert report.content["scenarios"] == []
