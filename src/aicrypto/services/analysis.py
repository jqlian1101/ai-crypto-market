"""分析运行与报告读取、发布、反馈用例。"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aicrypto.common.errors import AICryptoError, ErrorCode
from aicrypto.common.ids import new_analysis_id, new_feedback_id, new_run_id
from aicrypto.domain.analysis.report import AnalysisMode, AnalysisReportContent, AnalysisRunStatus
from aicrypto.infrastructure.database.models.analysis import MarketSnapshotRecord
from aicrypto.infrastructure.database.models.report import (
    AnalysisFeedbackRecord,
    AnalysisReportRecord,
    AnalysisRunRecord,
)


def _report_payload(record: AnalysisReportRecord) -> dict[str, Any]:
    return {
        key: getattr(record, key)
        for key in (
            "analysis_id",
            "symbol",
            "market_type",
            "market_snapshot_id",
            "snapshot_as_of",
            "available_as_of",
            "published_at",
            "analysis_mode",
            "status",
            "content",
            "previous_analysis_id",
            "supersedes_id",
            "agent_run_ids",
            "versions",
            "degraded",
            "degraded_reason",
            "schema_version",
            "created_at",
        )
    }


def get_report(db: Session, analysis_id: str) -> dict[str, Any]:
    record = db.get(AnalysisReportRecord, analysis_id)
    if record is None:
        raise AICryptoError(ErrorCode.ANALYSIS_NOT_FOUND, details={"analysis_id": analysis_id})
    payload = _report_payload(record)
    feedback = list(
        db.execute(
            select(AnalysisFeedbackRecord).where(
                AnalysisFeedbackRecord.analysis_id == analysis_id,
                AnalysisFeedbackRecord.review_kind == "USER",
            )
        ).scalars()
    )
    votes = [item.useful for item in feedback if item.useful is not None]
    payload["feedback_summary"] = {
        "count": len(votes),
        "useful_count": sum(votes),
        "not_useful_count": len(votes) - sum(votes),
    }
    return payload


def latest_report(
    db: Session, *, symbol: str | None = None, market_type: str | None = None
) -> dict[str, Any] | None:
    query = select(AnalysisReportRecord).where(
        AnalysisReportRecord.published_at.is_not(None),
        AnalysisReportRecord.analysis_mode == AnalysisMode.LIVE.value,
    )
    if symbol:
        query = query.where(AnalysisReportRecord.symbol == symbol.strip().upper())
    if market_type:
        query = query.where(AnalysisReportRecord.market_type == market_type.strip().upper())
    record = db.execute(
        query.order_by(AnalysisReportRecord.published_at.desc()).limit(1)
    ).scalar_one_or_none()
    return None if record is None else _report_payload(record)


def create_run(
    db: Session,
    *,
    symbol: str,
    market_type: str,
    trigger: str,
    now: dt.datetime,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    fields = {"symbol": symbol.strip().upper(), "market_type": market_type, "trigger": trigger}
    statement = (
        insert(AnalysisRunRecord)
        .values(
            run_id=new_run_id(now=now),
            analysis_id=new_analysis_id(),
            **fields,
            idempotency_key=idempotency_key,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=[AnalysisRunRecord.idempotency_key])
    )
    run = db.scalars(statement.returning(AnalysisRunRecord)).one_or_none()
    if run is None:
        run = db.scalars(
            select(AnalysisRunRecord).where(AnalysisRunRecord.idempotency_key == idempotency_key)
        ).one()
        if any(getattr(run, key) != value for key, value in fields.items()):
            raise AICryptoError(ErrorCode.COMMON_IDEMPOTENCY_KEY_REUSED)
    return _run_payload(run)


def get_run(db: Session, run_id: str) -> dict[str, Any]:
    run = db.get(AnalysisRunRecord, run_id)
    if run is None:
        raise AICryptoError(ErrorCode.ANALYSIS_NOT_FOUND, details={"run_id": run_id})
    return _run_payload(run)


def _run_payload(run: AnalysisRunRecord) -> dict[str, Any]:
    return {
        key: getattr(run, key)
        for key in (
            "run_id",
            "analysis_id",
            "symbol",
            "market_type",
            "trigger",
            "status",
            "analysis_mode",
            "market_snapshot_id",
            "error",
            "started_at",
            "finished_at",
            "created_at",
        )
    }


def create_report(
    db: Session,
    *,
    run_id: str,
    market_snapshot_id: str,
    content: AnalysisReportContent,
    status: AnalysisRunStatus = AnalysisRunStatus.COMPLETE,
    now: dt.datetime,
    versions: dict[str, str] | None = None,
    agent_run_ids: list[str] | None = None,
    degraded_reason: str | None = None,
) -> dict[str, Any]:
    run = db.get(AnalysisRunRecord, run_id, with_for_update=True)
    if run is None:
        raise AICryptoError(ErrorCode.ANALYSIS_NOT_FOUND, details={"run_id": run_id})
    if run.status != AnalysisRunStatus.RUNNING or status not in (
        AnalysisRunStatus.COMPLETE,
        AnalysisRunStatus.PARTIAL,
    ):
        raise AICryptoError(ErrorCode.COMMON_CONFLICT, message="当前运行不能发布报告")
    snapshot = db.get(MarketSnapshotRecord, market_snapshot_id)
    if snapshot is None:
        raise AICryptoError(
            ErrorCode.ANALYSIS_SNAPSHOT_MISSING, details={"market_snapshot_id": market_snapshot_id}
        )
    if (snapshot.symbol, snapshot.market_type) != (run.symbol, run.market_type):
        raise AICryptoError(ErrorCode.COMMON_CONFLICT, message="快照与分析标的不一致")
    analysis_mode = AnalysisMode(run.analysis_mode)
    if analysis_mode == AnalysisMode.LIVE and snapshot.available_as_of is None:
        raise AICryptoError(ErrorCode.EVAL_LOOKAHEAD_VIOLATION, message="实时快照缺少可用时间")
    for evidence in (*content.evidence, *content.counter_evidence):
        value: Any = snapshot.content
        try:
            for segment in evidence.source_path.split("."):
                value = value[int(segment)] if isinstance(value, list) else value[segment]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AICryptoError(ErrorCode.LLM_SCHEMA_INVALID, message="证据路径不存在") from exc
        if (
            (evidence.value is not None and str(evidence.value) != str(value))
            or evidence.snapshot_id != market_snapshot_id
            or (evidence.observed_at is not None and evidence.observed_at > snapshot.as_of)
            or (
                evidence.available_at is not None
                and snapshot.available_as_of is not None
                and evidence.available_at > snapshot.available_as_of
            )
        ):
            raise AICryptoError(ErrorCode.EVAL_LOOKAHEAD_VIOLATION, message="报告证据超出快照边界")
    previous = db.execute(
        select(AnalysisReportRecord)
        .where(
            AnalysisReportRecord.symbol == snapshot.symbol,
            AnalysisReportRecord.market_type == snapshot.market_type,
            AnalysisReportRecord.analysis_mode == analysis_mode.value,
            AnalysisReportRecord.published_at.is_not(None),
        )
        .order_by(AnalysisReportRecord.published_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    report = AnalysisReportRecord(
        analysis_id=run.analysis_id,
        symbol=snapshot.symbol,
        market_type=snapshot.market_type,
        market_snapshot_id=market_snapshot_id,
        snapshot_as_of=snapshot.as_of,
        available_as_of=snapshot.available_as_of,
        published_at=now,
        analysis_mode=analysis_mode.value,
        status=status.value,
        content=content.model_dump(mode="json"),
        previous_analysis_id=previous.analysis_id if previous else None,
        supersedes_id=None,
        agent_run_ids=agent_run_ids or [],
        versions=versions or {},
        degraded=status == AnalysisRunStatus.PARTIAL or bool(degraded_reason),
        degraded_reason=(
            degraded_reason
            if degraded_reason
            else (
                "仅使用已收盘 K 线，其他数据源尚未接入"
                if status == AnalysisRunStatus.PARTIAL
                else None
            )
        ),
        schema_version="1",
        created_at=now,
    )
    db.add(report)
    run.market_snapshot_id = market_snapshot_id
    run.status = status.value
    run.finished_at = now
    db.flush()
    return _report_payload(report)


def add_feedback(
    db: Session,
    *,
    analysis_id: str,
    useful: bool | None,
    reason: str | None,
    idempotency_key: str | None,
    now: dt.datetime,
) -> dict[str, Any]:
    if db.get(AnalysisReportRecord, analysis_id) is None:
        raise AICryptoError(ErrorCode.ANALYSIS_NOT_FOUND, details={"analysis_id": analysis_id})
    fields = {"analysis_id": analysis_id, "useful": useful, "reason": reason}
    statement = (
        insert(AnalysisFeedbackRecord)
        .values(
            feedback_id=new_feedback_id(),
            **fields,
            review_kind="USER",
            idempotency_key=idempotency_key,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=[AnalysisFeedbackRecord.idempotency_key])
    )
    feedback = db.scalars(statement.returning(AnalysisFeedbackRecord)).one_or_none()
    if feedback is None:
        feedback = db.scalars(
            select(AnalysisFeedbackRecord).where(
                AnalysisFeedbackRecord.idempotency_key == idempotency_key
            )
        ).one()
        if any(getattr(feedback, key) != value for key, value in fields.items()):
            raise AICryptoError(ErrorCode.COMMON_IDEMPOTENCY_KEY_REUSED)
    return {
        "feedback_id": feedback.feedback_id,
        "analysis_id": analysis_id,
        "created_at": feedback.created_at,
    }
