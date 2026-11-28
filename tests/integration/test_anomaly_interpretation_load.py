"""升级投递加载 AI 解读的真库集成测试（PRD 22.4.4.1）。

锁住的不变量：

- run → report 查找正确，COMPLETE 无降级时返回 FULL 与报告内容；
- 报告降级（degraded）时返回 BRIEF，且带降级原因，不回填解读内容；
- run 记录缺失或报告未发布时返回 BRIEF，不伪造内容；
- 首投（revision 0）不查询，直接 NONE。

需设置 ``ANOMALY_TEST_DATABASE_URL``（已迁移到 head）。写入在事务内回滚。
"""

from __future__ import annotations

import datetime as dt
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from aicrypto.domain.anomaly.revision import RevisionBody
from aicrypto.infrastructure.database.models.analysis import MarketSnapshotRecord
from aicrypto.infrastructure.database.models.anomaly import AnomalyEvent, AnomalyRule
from aicrypto.infrastructure.database.models.report import AnalysisReportRecord, AnalysisRunRecord
from aicrypto.infrastructure.notifications.service import _load_interpretation

pytestmark = pytest.mark.integration

SYMBOL = "ZZINTERPUSDT"
CUTOFF = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)
FINGERPRINT = "ae_interptest00000000000000"
EVENT_ID = f"{FINGERPRINT}_r1"
SNAPSHOT_ID = "snap_interptest"
CONTENT = {"summary": "测试摘要", "market_bias": "偏多"}


@pytest.fixture
def db() -> Session:
    url = os.getenv("ANOMALY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 ANOMALY_TEST_DATABASE_URL 以执行解读加载集成测试")
    engine = create_engine(url)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def _seed_rule_and_snapshot(db: Session) -> None:
    db.add(
        AnomalyRule(
            rule_id="PRICE_PCT_5M_CRIT",
            rule_version="rv_interptest",
            category="PRICE",
            description="解读加载集成测试规则",
            severity="CRITICAL",
            push_mode="PUSH",
            params={},
            asset_overrides={},
            market_type="USDM_FUTURES",
            enabled=True,
            effective_from=CUTOFF,
            created_at=CUTOFF,
            change_note="test",
        )
    )
    db.flush()
    db.add(
        MarketSnapshotRecord(
            snapshot_id=SNAPSHOT_ID,
            symbol=SYMBOL,
            market_type="USDM_FUTURES",
            as_of=CUTOFF,
            created_at=CUTOFF,
            data_quality="OK",
            data_quality_detail={},
            feature_version="fv_test",
            schema_version="1",
            content={},
            source_watermarks={},
        )
    )
    db.flush()


def _seed_event(db: Session, *, revision: int, run_id: str | None) -> AnomalyEvent:
    event = AnomalyEvent(
        anomaly_event_id=EVENT_ID,
        detected_at=CUTOFF,
        symbol=SYMBOL,
        market_type="USDM_FUTURES",
        rule_id="PRICE_PCT_5M_CRIT",
        rule_version="rv_interptest",
        severity="CRITICAL",
        detected_by="RULE_ENGINE",
        window_start=CUTOFF,
        window_end=CUTOFF,
        metrics={},
        threshold={},
        direction="UP",
        cooldown_key=f"{SYMBOL}:PRICE_PCT_5M_CRIT",
        fingerprint=FINGERPRINT,
        revision=revision,
        revision_reason="AI_INTERPRETATION_READY" if revision else "INITIAL_DETERMINISTIC",
        llm_interpretation_id=run_id,
        notification_status="PENDING",
    )
    db.add(event)
    db.flush()
    return event


def _seed_run_and_report(
    db: Session,
    *,
    analysis_id: str,
    run_id: str,
    status: str,
    degraded_reason: str | None,
) -> None:
    db.add(
        AnalysisRunRecord(
            run_id=run_id,
            analysis_id=analysis_id,
            symbol=SYMBOL,
            market_type="USDM_FUTURES",
            trigger="ANOMALY",
            idempotency_key=f"anomaly:{run_id}",
            created_at=CUTOFF,
        )
    )
    db.flush()
    db.add(
        AnalysisReportRecord(
            analysis_id=analysis_id,
            symbol=SYMBOL,
            market_type="USDM_FUTURES",
            market_snapshot_id=SNAPSHOT_ID,
            snapshot_as_of=CUTOFF,
            status=status,
            content=CONTENT,
            versions={},
            degraded=degraded_reason is not None,
            degraded_reason=degraded_reason,
            created_at=CUTOFF,
        )
    )
    db.flush()


def test_complete_report_returns_full(db: Session) -> None:
    _seed_rule_and_snapshot(db)
    _seed_run_and_report(
        db, analysis_id="ana_full", run_id="run_full", status="COMPLETE", degraded_reason=None
    )
    event = _seed_event(db, revision=1, run_id="run_full")

    body, interpretation = _load_interpretation(db, event)

    assert body is RevisionBody.FULL
    assert interpretation == CONTENT


def test_degraded_report_returns_brief_with_reason(db: Session) -> None:
    _seed_rule_and_snapshot(db)
    _seed_run_and_report(
        db,
        analysis_id="ana_deg",
        run_id="run_deg",
        status="PARTIAL",
        degraded_reason="LLM 超时",
    )
    event = _seed_event(db, revision=1, run_id="run_deg")

    body, interpretation = _load_interpretation(db, event)

    assert body is RevisionBody.BRIEF
    assert interpretation == {"degraded_reason": "LLM 超时"}


def test_missing_run_returns_brief(db: Session) -> None:
    _seed_rule_and_snapshot(db)
    event = _seed_event(db, revision=1, run_id="run_not_exist")

    body, interpretation = _load_interpretation(db, event)

    assert body is RevisionBody.BRIEF
    assert interpretation == {"degraded_reason": "解读记录缺失"}


def test_run_without_report_returns_brief(db: Session) -> None:
    _seed_rule_and_snapshot(db)
    db.add(
        AnalysisRunRecord(
            run_id="run_noreport",
            analysis_id="ana_noreport",
            symbol=SYMBOL,
            market_type="USDM_FUTURES",
            trigger="ANOMALY",
            idempotency_key="anomaly:run_noreport",
            created_at=CUTOFF,
        )
    )
    db.flush()
    event = _seed_event(db, revision=1, run_id="run_noreport")

    body, interpretation = _load_interpretation(db, event)

    assert body is RevisionBody.BRIEF
    assert interpretation == {"degraded_reason": "报告未发布"}


def test_first_delivery_does_not_query(db: Session) -> None:
    _seed_rule_and_snapshot(db)
    event = _seed_event(db, revision=0, run_id=None)

    body, interpretation = _load_interpretation(db, event)

    assert body is RevisionBody.NONE
    assert interpretation is None
