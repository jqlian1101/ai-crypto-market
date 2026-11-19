"""在已迁移 PostgreSQL 中验证生命周期；所有写入在测试后回滚。"""

import os
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from aicrypto.api.app import create_app
from aicrypto.common.clock import now
from aicrypto.config.settings import Settings
from aicrypto.domain.analysis.report import AnalysisReportContent
from aicrypto.infrastructure.database.models.market import MarketCandle
from aicrypto.infrastructure.database.models.report import AnalysisRunRecord
from aicrypto.infrastructure.database.session import get_db
from aicrypto.services import analysis_worker

pytestmark = pytest.mark.integration


def test_analysis_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.getenv("ANALYSIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 ANALYSIS_TEST_DATABASE_URL 以执行 PostgreSQL 集成测试")
    settings = Settings(
        _env_file=None, DATABASE_URL=url, REDIS_URL="redis://127.0.0.1:6379/0", AUTH_ENABLED=False
    )
    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        db = Session(connection, expire_on_commit=False)

        @contextmanager
        def scope(_settings):
            yield db
            db.flush()

        def override_db():
            yield db

        monkeypatch.setattr(analysis_worker, "session_scope", scope)
        monkeypatch.setattr(
            analysis_worker,
            "generate_report",
            lambda *args: AnalysisReportContent(
                summary="测试报告",
                market_bias="NEUTRAL",
                time_horizon="4h",
            ),
        )
        app = create_app(settings)
        app.dependency_overrides[get_db] = override_db
        try:
            moment = now()
            db.add(
                MarketCandle(
                    exchange="binance",
                    symbol="TESTLIFECYCLEUSDT",
                    market_type="SPOT",
                    timeframe="1h",
                    open_time=moment - timedelta(hours=2),
                    close_time=moment - timedelta(hours=1),
                    open=Decimal("100"),
                    high=Decimal("110"),
                    low=Decimal("90"),
                    close=Decimal("105"),
                    volume=Decimal("5"),
                    quote_volume=Decimal("500"),
                    trades_count=10,
                    exchange_ts=moment - timedelta(hours=1),
                    received_at=moment - timedelta(minutes=59),
                    processed_at=moment - timedelta(minutes=58),
                    is_closed=True,
                    source="REST",
                )
            )
            db.flush()
            with TestClient(app) as client:
                body = {"symbol": "TESTLIFECYCLEUSDT"}
                headers = {"Idempotency-Key": "integration-analysis-run"}
                created = client.post("/api/v1/analysis/runs", json=body, headers=headers)
                assert created.status_code == 202, created.text
                run = created.json()["data"]
                repeated = client.post("/api/v1/analysis/runs", json=body, headers=headers)
                assert repeated.json()["data"]["run_id"] == run["run_id"]
                conflict = client.post(
                    "/api/v1/analysis/runs", json={"symbol": "OTHER"}, headers=headers
                )
                assert conflict.status_code == 409
                assert analysis_worker.process_one(settings)
                state = client.get(run["status_url"]).json()["data"]
                assert state["status"] == "PARTIAL"
                report = client.get(f"/api/v1/analysis/{run['analysis_id']}")
                assert report.status_code == 200, report.text
                assert report.json()["data"]["degraded"]
                assert report.json()["data"]["content"]["summary"] == "测试报告"
                latest = client.get("/api/v1/analysis/latest?symbol=TESTLIFECYCLEUSDT")
                assert latest.json()["data"]["analysis_id"] == run["analysis_id"]
                path = f"/api/v1/analysis/{run['analysis_id']}/feedback"
                vote_headers = {"Idempotency-Key": "integration-vote"}
                vote = client.post(path, json={"useful": True}, headers=vote_headers)
                assert vote.status_code == 200, vote.text
                again = client.post(path, json={"useful": True}, headers=vote_headers)
                assert vote.json() == again.json() or vote.json()["data"] == again.json()["data"]
                assert (
                    client.post(path, json={"useful": False}, headers=vote_headers).status_code
                    == 409
                )
                assert (
                    client.get(f"/api/v1/analysis/{run['analysis_id']}").json()["data"][
                        "feedback_summary"
                    ]["count"]
                    == 1
                )
                assert client.post(path, json={}).status_code == 422
                db.add(
                    AnalysisRunRecord(
                        run_id="run_expired_test",
                        analysis_id="analysis_expired_test",
                        symbol="EXPIRED",
                        market_type="SPOT",
                        trigger="MANUAL",
                        status="RUNNING",
                        analysis_mode="LIVE",
                        created_at=moment - timedelta(hours=1),
                        started_at=moment - timedelta(hours=1),
                    )
                )
                db.flush()
                analysis_worker.claim_run(settings)
                db.expire_all()
                assert db.get(AnalysisRunRecord, "run_expired_test").status == "TIMEOUT"
                missing = client.post(
                    "/api/v1/analysis/runs", json={"symbol": "NO_DATA_TEST"}
                ).json()["data"]
                assert analysis_worker.process_one(settings)
                state = client.get(missing["status_url"]).json()["data"]
                assert state["status"] == "FAILED"
                assert state["error"]["code"] == "ANALYSIS_SNAPSHOT_MISSING"
        finally:
            db.close()
            transaction.rollback()
    engine.dispose()
