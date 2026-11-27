"""成本报表的真库验证（PRD 24.2 / 24.4）。

**背景**：`llm_cost_records` 表已有真实数据，但**没有任何东西读它**——
又一个「有表无读方」（L-30 那一族）。

**为什么必须真库**：单元测试用的是**内存构造**的 ``CostRow``，
验证不了三件只有真库能暴露的事：

1. **LEFT JOIN 的正确性** —— ``llm_cost_records`` 没有 ``prompt_version``
   与 ``status`` 列，必须从 ``model_runs`` 关联取；
2. **JSONB 的 ``priced`` 标记确实能读出来** —— 快照是 JSONB，
   类型行为与 Python dict 不同；
3. **孤儿成本行不会被 INNER JOIN 悄悄抹掉** ——
   成本行的存在本身是**必须被看见的事实**。

**未定价行的处置**：单价缺失时 ``estimated_cost_usd`` 只能写 0
（该列 NOT NULL）。真库测试要确认这类行**不会**污染费用总额，
但其 token 仍计入总量。
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aicrypto.infrastructure.database.models.analysis import LLMCostRecord, ModelRun
from aicrypto.services.cost_report import build_cost_report, fetch_cost_rows

pytestmark = pytest.mark.integration

#: 区间起点——固定值，避免依赖真实时钟
SINCE = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
UNTIL = dt.datetime(2100, 1, 1, tzinfo=dt.UTC)


@pytest.fixture
def db_env():
    """真库环境；未配置测试库则跳过。"""
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


def _insert(
    db: Session,
    *,
    model_id: str,
    priced: bool,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cost_usd: str = "0.0105",
    tier: str | None = "TIER_STANDARD",
    agent_name: str | None = "master",
    prompt_version: str | None = "v1",
    status: str | None = "SUCCESS",
) -> tuple[str, str]:
    """写入一对 ``model_runs`` + ``llm_cost_records``，返回两个 ID。"""
    model_run_id = f"mrun_{uuid.uuid4().hex}"
    cost_id = f"lcost_{uuid.uuid4().hex}"
    created_at = dt.datetime(2026, 6, 1, 12, 0, 0, tzinfo=dt.UTC)
    db.add(
        ModelRun(
            model_run_id=model_run_id,
            agent_run_id=None,
            provider="cloud",
            model_id=model_id,
            prompt_version=prompt_version or "v1",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=None,
            latency_ms=100,
            retry_count=0,
            status=status or "SUCCESS",
            error=None,
            created_at=created_at,
        )
    )
    snapshot: dict = {
        "model_id": model_id,
        "provider": "cloud",
        "currency": "USD",
        "priced": priced,
    }
    if priced:
        snapshot["pricing_version"] = "v1"
        snapshot["input_usd_per_mtok"] = "3.0"
        snapshot["output_usd_per_mtok"] = "15.0"
    db.add(
        LLMCostRecord(
            id=cost_id,
            created_at=created_at,
            model_run_id=model_run_id,
            model_id=model_id,
            agent_name=agent_name,
            tier=tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=Decimal(cost_usd),
            price_snapshot=snapshot,
            is_degraded=False,
        )
    )
    db.flush()
    return model_run_id, cost_id


def test_report_reads_prompt_version_from_model_runs(db_env: Session) -> None:
    """``prompt_version`` 只能从 ``model_runs`` 取到（成本表没这列）。"""
    _insert(db_env, model_id="m1", priced=True, prompt_version="v7")
    rows = fetch_cost_rows(db_env, since=SINCE, until=UNTIL)
    matching = [r for r in rows if r.model_id == "m1"]
    assert matching, "刚插入的成本行没被取到"
    assert matching[0].prompt_version == "v7"


def test_report_reads_status_from_model_runs(db_env: Session) -> None:
    """``status`` 同样只在 ``model_runs`` 上。"""
    _insert(db_env, model_id="m2", priced=True, status="FAILED")
    rows = fetch_cost_rows(db_env, since=SINCE, until=UNTIL)
    matching = [r for r in rows if r.model_id == "m2"]
    assert matching[0].status == "FAILED"


def test_report_reads_priced_flag_from_jsonb(db_env: Session) -> None:
    """JSONB 里的 ``priced`` 标记必须能正确读出布尔值。"""
    _insert(db_env, model_id="m3", priced=True)
    _insert(db_env, model_id="m4", priced=False)
    rows = fetch_cost_rows(db_env, since=SINCE, until=UNTIL)
    by_model = {r.model_id: r for r in rows}
    assert by_model["m3"].priced is True
    assert by_model["m4"].priced is False


def test_report_excludes_unpriced_cost_from_total(db_env: Session) -> None:
    """真库验证：未定价行的 0 费用**不得**混进总额。

    这是单元测试之外的最关键一条——
    它验证的是「数据库里真的存着 ``priced=false`` 的行」，
    而聚合逻辑确实把它们排除了。
    """
    _insert(db_env, model_id="priced-model", priced=True, cost_usd="0.0105")
    _insert(
        db_env,
        model_id="unpriced-model",
        priced=False,
        cost_usd="0",
        input_tokens=5000,
        output_tokens=2500,
    )
    rows = fetch_cost_rows(db_env, since=SINCE, until=UNTIL)

    unpriced = [r for r in rows if r.model_id == "unpriced-model"]
    assert unpriced, "未定价行没被取到"
    # 未定价行在库里确实记着 0 —— 这正是「直接 SUM 就会低估」的原因
    assert unpriced[0].cost_usd == Decimal("0")

    # 只统计本次插入的两行
    relevant = [r for r in rows if r.model_id in {"priced-model", "unpriced-model"}]
    sub = build_cost_report(relevant, since=SINCE, until=UNTIL)
    assert sub.total.cost_usd == Decimal("0.0105")
    assert sub.total.unpriced_calls == 1
    # token 仍计入
    assert sub.total.input_tokens == 1000 + 5000
    assert sub.has_unpriced is True


def test_orphan_cost_row_is_not_dropped(db_env: Session) -> None:
    """孤儿成本行（没有对应的 ``model_runs``）**不得**被 JOIN 抹掉。

    成本行的存在是一个**必须被看见的事实**——
    INNER JOIN 会让它凭空消失，费用凭空减少，
    而读者**看不出少了东西**。
    """
    cost_id = f"lcost_{uuid.uuid4().hex}"
    db_env.add(
        LLMCostRecord(
            id=cost_id,
            created_at=dt.datetime(2026, 6, 1, 12, 0, 0, tzinfo=dt.UTC),
            model_run_id="mrun_does_not_exist",
            model_id="orphan-model",
            agent_name=None,
            tier=None,
            input_tokens=123,
            output_tokens=45,
            estimated_cost_usd=Decimal("0.0005"),
            price_snapshot={"model_id": "orphan-model", "priced": True},
            is_degraded=False,
        )
    )
    db_env.flush()

    rows = fetch_cost_rows(db_env, since=SINCE, until=UNTIL)
    orphans = [r for r in rows if r.model_id == "orphan-model"]
    assert orphans, "孤儿成本行被 JOIN 抹掉了——这会让费用凭空减少"
    assert orphans[0].prompt_version is None
    assert orphans[0].status is None
    assert orphans[0].cost_usd == Decimal("0.0005")


def test_report_filters_by_time_window(db_env: Session) -> None:
    """区间过滤必须生效——否则「本月花了多少」会答成「历史一共花了多少」。"""
    _insert(db_env, model_id="in-window", priced=True)
    rows = fetch_cost_rows(
        db_env,
        since=dt.datetime(2027, 1, 1, tzinfo=dt.UTC),
        until=dt.datetime(2027, 12, 31, tzinfo=dt.UTC),
    )
    assert [r for r in rows if r.model_id == "in-window"] == []


def test_report_groups_by_model_in_real_db(db_env: Session) -> None:
    """真库聚合：按模型分组并按费用降序。"""
    _insert(db_env, model_id="cheap-model", priced=True, cost_usd="0.0010")
    _insert(db_env, model_id="pricey-model", priced=True, cost_usd="0.9000")
    rows = fetch_cost_rows(db_env, since=SINCE, until=UNTIL)
    report = build_cost_report(rows, since=SINCE, until=UNTIL)
    by_model = {b.key: b for b in report.by_model}
    assert by_model["pricey-model"].cost_usd == Decimal("0.9000")
    assert by_model["cheap-model"].cost_usd == Decimal("0.0010")


def test_existing_rows_are_readable(db_env: Session) -> None:
    """库里**已有的**真实成本行必须能被正常读取。

    这条用例防止「报表只对新写入的数据有效」——
    历史数据才是报表的主要价值。
    """
    existing = (
        db_env.execute(select(LLMCostRecord).order_by(LLMCostRecord.created_at.desc()).limit(5))
        .scalars()
        .all()
    )
    if not existing:
        pytest.skip("库中没有历史成本行")
    rows = fetch_cost_rows(db_env, since=SINCE, until=UNTIL)
    read_ids = {r.model_run_id for r in rows}
    for record in existing:
        assert record.model_run_id in read_ids, f"历史成本行没被读到：{record.id}"
