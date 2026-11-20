"""异动事件 → 飞书卡片渲染（PRD 22.4.3）。

只使用异动事件已有字段，不编造行情数据。
"""

from __future__ import annotations

from typing import Any

from aicrypto.infrastructure.database.models.anomaly import AnomalyEvent

SEVERITY_TEMPLATE = {
    "CRITICAL": "red",
    "WARN": "orange",
    "INFO": "blue",
}


def render_anomaly_card(event: AnomalyEvent) -> dict[str, Any]:
    """渲染异动快报卡片。``revision > 0`` 时使用区分标题，避免与首投混淆（22.4.4.1）。"""
    title = f"{event.symbol} 异动快报"
    if event.revision > 0:
        title = f"{event.symbol} 异动快报（AI 解读已更新）"

    direction = event.direction or "-"
    body = (
        f"**规则**：{event.rule_id}（{event.rule_version}）\n"
        f"**方向**：{direction}\n"
        f"**窗口**：{event.window_start:%Y-%m-%d %H:%M} ~ {event.window_end:%H:%M} UTC\n"
        f"**检测时间**：{event.detected_at:%Y-%m-%d %H:%M:%S} UTC"
    )
    if event.revision_reason:
        body += f"\n**版本说明**：{event.revision_reason}"

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": SEVERITY_TEMPLATE.get(event.severity, "blue"),
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": body}},
            ],
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
