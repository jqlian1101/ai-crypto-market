"""模型单价同步的真库验证（PRD 24.2 / 30.4）。

**背景**���``base.toml`` 的 ``[llm.model_pricing]`` 段早已存在，
但**没有任何代码读取它**。本测试验证同步链路真的把它写进 ``model_pricing`` 表。

关键不变量：

1. 同步是**幂等**的——重复跑不产生重复行；
2. 同版本号下单价变了**必须报错**——原地覆盖会让历史成本的版本号指向新价格；
3. 同步后 ``resolve_pricing`` 能查到单价，成本可被真实计算。
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aicrypto.config.model_pricing import (
    PricingConfigError,
    load_model_pricing,
    sync_model_pricing,
)
from aicrypto.infrastructure.database.models.analysis import ModelPricing
from aicrypto.services.llm_cost import resolve_pricing

pytestmark = pytest.mark.integration


@pytest.fixture
def db_env():
    url = os.getenv("ANALYSIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 ANALYSIS_TEST_DATABASE_URL 以执行 PostgreSQL 集成测试")
    engine = create_engine(url)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(connection, expire_on_commit=False)
    # 先清掉 model_pricing 的既有行：测试必须不依赖数据库初始状态
    # （例如开发者手动跑过 scripts/sync_model_pricing.py 之后库里就有数据了）。
    session.query(ModelPricing).delete()
    session.flush()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        engine.dispose()


def test_sync_inserts_pricing(db_env: Session) -> None:
    """同步把 base.toml 的单价写进表。"""
    stats = sync_model_pricing(db_env, pricing_version="v1")
    assert stats["inserted"] == len(load_model_pricing())
    assert stats["inserted"] > 0

    rows = list(db_env.scalars(select(ModelPricing)))
    assert {row.model_id for row in rows} >= {"claude-haiku", "claude-sonnet"}
    by_id = {row.model_id: row for row in rows}
    assert by_id["claude-haiku"].input_usd_per_mtok == Decimal("0.10")
    assert by_id["claude-haiku"].output_usd_per_mtok == Decimal("0.50")
    assert by_id["claude-haiku"].pricing_version == "v1"
    assert by_id["claude-haiku"].currency == "USD"


def test_sync_is_idempotent(db_env: Session) -> None:
    """重复同步不产生重复行。"""
    sync_model_pricing(db_env, pricing_version="v1")
    second = sync_model_pricing(db_env, pricing_version="v1")
    assert second["inserted"] == 0
    assert second["skipped"] > 0

    rows = list(db_env.scalars(select(ModelPricing)))
    assert len(rows) == len({(row.model_id, row.pricing_version) for row in rows})


def test_changed_price_on_same_version_raises(db_env: Session) -> None:
    """同版本号下单价变了必须报错——必须递增版本号。"""
    sync_model_pricing(db_env, pricing_version="v1")
    row = db_env.execute(
        select(ModelPricing).where(ModelPricing.model_id == "claude-haiku")
    ).scalar_one()
    row.input_usd_per_mtok = Decimal("999.0")
    db_env.flush()

    with pytest.raises(PricingConfigError, match="pricing_version"):
        sync_model_pricing(db_env, pricing_version="v1")


def test_new_version_creates_new_row(db_env: Session) -> None:
    """递增版本号产生新行，旧行保留——历史成本仍指向旧版本。"""
    sync_model_pricing(db_env, pricing_version="v1")
    stats = sync_model_pricing(db_env, pricing_version="v2")
    assert stats["inserted"] > 0

    rows = list(db_env.scalars(select(ModelPricing)))
    versions = {(row.model_id, row.pricing_version) for row in rows}
    assert ("claude-haiku", "v1") in versions
    assert ("claude-haiku", "v2") in versions


def test_resolve_pricing_finds_synced_row(db_env: Session) -> None:
    """同步后 ``resolve_pricing`` 能查到单价。"""
    sync_model_pricing(db_env, pricing_version="v1")
    entry = resolve_pricing(
        db_env,
        model_id="claude-haiku",
        as_of=dt.datetime(2026, 12, 1, tzinfo=dt.UTC),
    )
    assert entry is not None
    assert entry.input_usd_per_mtok == Decimal("0.10")
    assert entry.pricing_version == "v1"


def test_resolve_pricing_returns_none_for_unknown_model(db_env: Session) -> None:
    """未知模型返回 ``None``（成本未知，不是 0）。"""
    sync_model_pricing(db_env, pricing_version="v1")
    entry = resolve_pricing(
        db_env,
        model_id="not-a-real-model",
        as_of=dt.datetime(2026, 12, 1, tzinfo=dt.UTC),
    )
    assert entry is None


def test_resolve_pricing_before_effective_from_returns_none(db_env: Session) -> None:
    """在生效时间之前查不到单价——不能用未来的价格算过去的成本。"""
    sync_model_pricing(db_env, pricing_version="v1")
    entry = resolve_pricing(
        db_env,
        model_id="claude-haiku",
        as_of=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )
    assert entry is None


def test_resolve_pricing_picks_newest_effective(db_env: Session) -> None:
    """多个版本时取 as_of 时点生效的**最新**一条。

    排序主键是 ``effective_from``——生效时间才是「哪一版在用」的决定因素，
    版本号只在生效时间相同时做兜底排序。
    """
    sync_model_pricing(db_env, pricing_version="v1")
    later = dt.datetime(2026, 12, 1, tzinfo=dt.UTC)
    db_env.add(
        ModelPricing(
            model_id="claude-haiku",
            pricing_version="v2",
            provider="anthropic",
            input_usd_per_mtok=Decimal("0.20"),
            output_usd_per_mtok=Decimal("1.00"),
            currency="USD",
            effective_from=later,
            created_at=later,
        )
    )
    db_env.flush()
    entry = resolve_pricing(
        db_env,
        model_id="claude-haiku",
        as_of=dt.datetime(2027, 1, 1, tzinfo=dt.UTC),
    )
    assert entry is not None
    assert entry.pricing_version == "v2"
    assert entry.input_usd_per_mtok == Decimal("0.20")


def test_resolve_pricing_uses_effective_date_not_version_order(db_env: Session) -> None:
    """生效时间更早的版本即使版本号更大也不是当前价。

    这是「生效时间优先」的直接断言：v1（生效 2026-11-01）与
    v2（生效 2020-01-01）并存时，当前时点应取 v1。
    """
    sync_model_pricing(db_env, pricing_version="v1")
    base = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    db_env.add(
        ModelPricing(
            model_id="claude-haiku",
            pricing_version="v2",
            provider="anthropic",
            input_usd_per_mtok=Decimal("0.20"),
            output_usd_per_mtok=Decimal("1.00"),
            currency="USD",
            effective_from=base,
            created_at=base,
        )
    )
    db_env.flush()
    entry = resolve_pricing(
        db_env,
        model_id="claude-haiku",
        as_of=dt.datetime(2026, 12, 1, tzinfo=dt.UTC),
    )
    assert entry is not None
    assert entry.pricing_version == "v1"
    assert entry.input_usd_per_mtok == Decimal("0.10")
