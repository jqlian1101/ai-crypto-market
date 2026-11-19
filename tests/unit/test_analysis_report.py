from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aicrypto.domain.analysis.report import (
    AnalysisMode,
    AnalysisReportContent,
    AnalysisRunStatus,
    EvidenceRef,
    ScenarioProbability,
)
from aicrypto.domain.analysis.snapshot import MarketSnapshot, SnapshotContent


def test_scenario_probabilities_require_complete_distribution() -> None:
    assert ScenarioProbability(up=0.5, down=0.2, flat=0.3).up == 0.5
    with pytest.raises(ValidationError):
        ScenarioProbability(up=0.5, down=0.2, flat=0.2)


def test_report_content_accepts_traceable_evidence() -> None:
    evidence = EvidenceRef(
        evidence_id="ev_1",
        source_type="snapshot",
        source_path="derivatives.funding_rate",
        value="0.0002",
        unit="rate",
        observed_at=datetime(2026, 11, 18, tzinfo=UTC),
        available_at=datetime(2026, 11, 18, tzinfo=UTC),
        snapshot_id="snap_test",
        data_quality="GOOD",
        interpretation="Funding remains positive.",
    )
    content = AnalysisReportContent(
        summary="Market is mixed.",
        market_bias="NEUTRAL",
        time_horizon="4h",
        evidence=[evidence],
        scenario_probabilities=ScenarioProbability(up=0.3, down=0.3, flat=0.4),
    )
    assert content.evidence[0].source_path == "derivatives.funding_rate"
    assert AnalysisMode.LIVE.value == "LIVE"
    assert AnalysisRunStatus.QUEUED.value == "QUEUED"


def test_provider_singleton_array_is_validated() -> None:
    import json

    from aicrypto.services.analysis_worker import parse_report_content

    payload = {"summary": "测试", "market_bias": "NEUTRAL", "time_horizon": "4h"}
    assert parse_report_content(json.dumps([payload])).summary == "测试"
    assert parse_report_content(json.dumps(payload)).summary == "测试"
    from aicrypto.common.errors import AICryptoError

    with pytest.raises(AICryptoError):
        parse_report_content(json.dumps([payload, payload]))
    with pytest.raises(ValidationError):
        parse_report_content(json.dumps([{"summary": "缺少必填字段"}]))


def test_snapshot_serializes_regime_and_available_time() -> None:
    snapshot = SnapshotContent(market_regime={"regime": "RANGE"})
    assert snapshot.to_dict()["market_regime"] == {"regime": "RANGE"}
    record = MarketSnapshot(
        symbol="BTCUSDT",
        market_type="SPOT",
        as_of=datetime(2026, 11, 18, tzinfo=UTC),
        content=snapshot,
    )
    assert record.to_dict()["available_as_of"] is None
    record.available_as_of = datetime(2026, 11, 19, tzinfo=UTC)
    assert record.to_dict()["available_as_of"] == "2026-11-19T00:00:00+00:00"
