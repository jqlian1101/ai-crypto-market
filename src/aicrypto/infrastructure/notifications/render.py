"""异动事件 → 飞书卡片渲染（PRD 22.4.3 / 22.4.4.1）。

- 首投（revision 0）只使用异动事件已有字段，不编造行情数据；
- 升级（revision 1）在此基础上叠加 AI 解读内容：
  - ``FULL``：摘要、方向概率、主要依据、失效条件；
  - ``BRIEF``：分析降级时的简短说明，明确标注未完整解读。
"""

from __future__ import annotations

from typing import Any

from aicrypto.domain.anomaly.revision import RevisionBody
from aicrypto.infrastructure.database.models.anomaly import AnomalyEvent

SEVERITY_TEMPLATE = {
    "CRITICAL": "red",
    "WARN": "orange",
    "INFO": "blue",
}

#: 摘要超过该长度截断，避免卡片超限（PRD 22.5：超长按优先级截断）。
SUMMARY_MAX_CHARS = 400
#: 失效条件最多展示条数。
MAX_INVALIDATION_ITEMS = 3


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _interpretation_lines(body: RevisionBody, interpretation: dict[str, Any] | None) -> list[str]:
    if body is RevisionBody.BRIEF or interpretation is None:
        reason = (interpretation or {}).get("degraded_reason") or "分析未完成"
        return [f"**AI 解读**：分析降级（{reason}），仅保留确定性快报。"]

    lines: list[str] = []
    summary = interpretation.get("summary")
    if summary:
        lines.append(f"**AI 摘要**：{_truncate(str(summary), SUMMARY_MAX_CHARS)}")
    bias = interpretation.get("market_bias")
    if bias:
        lines.append(f"**市场偏向**：{bias}")
    probs = interpretation.get("scenario_probabilities")
    if probs:
        lines.append(
            f"**情景概率**：上行 {probs['up']:.0%} / 下行 {probs['down']:.0%} / 震荡 {probs['flat']:.0%}"
            "（主观估计，非统计胜率）"
        )
    factors = interpretation.get("dominant_factors") or []
    if factors:
        lines.append("**主要依据**：" + "；".join(str(f) for f in factors[:3]))
    invalidations = interpretation.get("invalidation_conditions") or []
    if invalidations:
        items = [str(item) for item in invalidations[:MAX_INVALIDATION_ITEMS]]
        lines.append("**失效条件**：" + "；".join(items))
    return lines


def render_anomaly_card(
    event: AnomalyEvent,
    *,
    body: RevisionBody = RevisionBody.NONE,
    interpretation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """渲染异动卡片。``revision > 0`` 时使用区分标题并附 AI 解读（22.4.4.1）。"""
    title = f"{event.symbol} 异动快报"
    if event.revision > 0:
        title = f"{event.symbol} 异动快报（AI 解读已更新）"

    direction = event.direction or "-"
    body_text = (
        f"**规则**：{event.rule_id}（{event.rule_version}）\n"
        f"**方向**：{direction}\n"
        f"**窗口**：{event.window_start:%Y-%m-%d %H:%M} ~ {event.window_end:%H:%M} UTC\n"
        f"**检测时间**：{event.detected_at:%Y-%m-%d %H:%M:%S} UTC"
    )
    if event.revision_reason:
        body_text += f"\n**版本说明**：{event.revision_reason}"

    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body_text}},
    ]
    if event.revision > 0:
        lines = _interpretation_lines(body, interpretation)
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": SEVERITY_TEMPLATE.get(event.severity, "blue"),
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": elements,
        },
    }


def render_test_card() -> dict[str, Any]:
    """测试卡片：仅用于 ``POST /notifications/test``。"""
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": "推送测试"},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "这是一条测试消息，用于验证飞书渠道连通性。",
                    },
                },
            ],
        },
    }


__all__ = ["render_anomaly_card", "render_test_card"]
