"""异动 AI 解读升级轮次的纯判定（PRD 22.4.4.1 / 22.6）。

revision 1（``AI_INTERPRETATION_READY``）只在分析产出了可用结论时投递：

- 模型正常产出（无降级原因）→ ``FULL``：完整解读卡片；
- 超时/失败产生的部分报告（带降级原因）→ ``BRIEF``：仅一条简短说明；
- 分析失败或超时终态（无报告）→ ``NONE``：不发升级，首投已是最终消息。

本模块只做判定，不读写存储，也不渲染内容。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

REVISION_AI_INTERPRETATION = 1
REASON_AI_INTERPRETATION_READY = "AI_INTERPRETATION_READY"

#: 报告状态（``AnalysisRunStatus`` 的取值）。
_STATUS_WITH_REPORT = frozenset({"COMPLETE", "PARTIAL"})


class RevisionBody(StrEnum):
    FULL = "FULL"
    BRIEF = "BRIEF"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class RevisionDecision:
    body: RevisionBody
    reason: str

    @property
    def emit(self) -> bool:
        return self.body is not RevisionBody.NONE


def decide_ai_revision(*, run_status: str, degraded_reason: str | None) -> RevisionDecision:
    """根据分析终态决定是否生成 revision 1，以及用哪种正文。"""
    if run_status not in _STATUS_WITH_REPORT:
        return RevisionDecision(RevisionBody.NONE, f"RUN_{run_status}_NO_REPORT")
    if degraded_reason:
        return RevisionDecision(RevisionBody.BRIEF, "DEGRADED_PARTIAL")
    return RevisionDecision(RevisionBody.FULL, "MODEL_INTERPRETATION")


__all__ = [
    "REASON_AI_INTERPRETATION_READY",
    "REVISION_AI_INTERPRETATION",
    "RevisionBody",
    "RevisionDecision",
    "decide_ai_revision",
]
