"""AI 解读升级轮次判定测试（PRD 22.4.4.1）。"""

from __future__ import annotations

import pytest

from aicrypto.domain.anomaly.revision import RevisionBody, decide_ai_revision


def test_model_complete_report_gets_full_revision() -> None:
    decision = decide_ai_revision(run_status="COMPLETE", degraded_reason=None)
    assert decision.body is RevisionBody.FULL
    assert decision.emit


def test_normal_model_report_stored_as_partial_still_gets_full_revision() -> None:
    """正常模型产出在代码里仍以 PARTIAL 落库，不能因此被误判为降级。"""
    decision = decide_ai_revision(run_status="PARTIAL", degraded_reason=None)
    assert decision.body is RevisionBody.FULL


def test_degraded_partial_report_gets_brief_revision() -> None:
    decision = decide_ai_revision(run_status="PARTIAL", degraded_reason="LLM_TIMEOUT")
    assert decision.body is RevisionBody.BRIEF
    assert decision.emit


@pytest.mark.parametrize("status", ["FAILED", "TIMEOUT", "QUEUED", "RUNNING"])
def test_no_report_means_no_revision(status: str) -> None:
    decision = decide_ai_revision(run_status=status, degraded_reason=None)
    assert decision.body is RevisionBody.NONE
    assert not decision.emit
