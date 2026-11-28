"""AI 解读升级行的真库集成测试（PRD 22.4.4.1 / 22.6）。

锁住的不变量：

- 分析完成（COMPLETE/PARTIAL 无降级）后，恰好生成一条 ``revision=1`` 的事件，且与首投共享 fingerprint；
- 同一分析重跑不会重复插入（唯一约束 + ON CONFLICT）；
- 失败或超时终态不生成升级行。

需设置 ``ANOMALY_TEST_DATABASE_URL``（已迁移到 head）。写入在事务内回滚。
"""

from __future__ import annotations

import datetime as dt
import os

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aicrypto.infrastructure.database.models.anomaly import AnomalyEvent
from aicrypto.infrastructure.database.models.report import AnalysisRunRecord
from aicrypto.services.anomaly_revision import create_ai_revision

pytestmark = pytest.mark.integration

SYMBOL = "ZZREVUSDT"
CUTOFF = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)
FINGERPRINT = "ae_revtest0000000000000000"
BASE_ID = f"{FINGERPRINT}_r0"


@pytest.fixture
def db() -> Session:
    url = os.getenv("ANOMALY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 ANOMALY_TEST_DATABASE_URL 以执行升级行集成测试")
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


def _seed_base_event(db: Session) -> None:
    from aicrypto.infrastructure.database.models.anomaly import AnomalyRule

    db.add(
        AnomalyRule(
            rule_id="PRICE_PCT_5M_CRIT",
            rule_version="rv_revtest",
            category="PRICE",
            description="revision 集成测试规则",
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
    # 无 ORM relationship，SQLAlchemy 不保证按外键顺序 flush：父表必须先落库。
    db.flush()
    db.add(
        AnomalyEvent(
            anomaly_event_id=BASE_ID,
            detected_at=CUTOFF,
            symbol=SYMBOL,
            market_type="USDM_FUTURES",
            rule_id="PRICE_PCT_5M_CRIT",
            rule_version="rv_revtest",
            severity="CRITICAL",
            detected_by="RULE_ENGINE",
            window_start=CUTOFF,
            window_end=CUTOFF,
            metrics={},
            threshold={},
            direction="UP",
            cooldown_key=f"{SYMBOL}:PRICE_PCT_5M_CRIT",
            fingerprint=FINGERPRINT,
            revision=0,
            revision_reason="INITIAL_DETERMINISTIC",
            notification_status="SENT",
        )
    )
    db.flush()
    db.add(
        AnalysisRunRecord(
            run_id="run_revtest",
            analysis_id="ana_revtest",
            symbol=SYMBOL,
            market_type="USDM_FUTURES",
            trigger="ANOMALY",
            idempotency_key="anomaly:revtest",
            anomaly_event_id=BASE_ID,
            created_at=CUTOFF,
        )
    )
    db.flush()


def _revisions(db: Session) -> list[AnomalyEvent]:
    return list(
        db.scalars(
            select(AnomalyEvent)
            .where(AnomalyEvent.fingerprint == FINGERPRINT)
            .order_by(AnomalyEvent.revision)
        )
    )


def test_completed_analysis_creates_single_revision_one(db: Session) -> None:
    _seed_base_event(db)
    first = create_ai_revision(db, run_id="run_revtest", run_status="PARTIAL", degraded_reason=None)
    second = create_ai_revision(
        db, run_id="run_revtest", run_status="PARTIAL", degraded_reason=None
    )
    db.flush()

    revisions = _revisions(db)
    assert [r.revision for r in revisions] == [0, 1]
    assert revisions[1].revision_reason == "AI_INTERPRETATION_READY"
    assert revisions[1].notification_status == "PENDING"
    assert first is not None
    assert second is None, "同一分析重跑不得重复插入升级行"


def test_failed_analysis_creates_no_revision(db: Session) -> None:
    _seed_base_event(db)
    result = create_ai_revision(db, run_id="run_revtest", run_status="FAILED", degraded_reason=None)
    db.flush()

    assert result is None
    assert [r.revision for r in _revisions(db)] == [0]
