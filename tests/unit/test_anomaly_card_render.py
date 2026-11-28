"""异动卡片渲染测试（PRD 22.4.3 / 22.4.4.1）。

锁住的不变量：

- 首投（revision 0）卡片不含 AI 解读段落，标题不变；
- 升级（revision 1）FULL 解读包含摘要、偏向、概率、依据、失效条件；
- 情景概率标注为主观估计；
- BRIEF 明确标注降级，不编造解读内容；
- 超长摘要被截断。
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any

from aicrypto.domain.anomaly.revision import RevisionBody
from aicrypto.infrastructure.notifications.render import SUMMARY_MAX_CHARS, render_anomaly_card

T0 = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)


def _event(revision: int = 0, revision_reason: str = "INITIAL_DETERMINISTIC") -> SimpleNamespace:
    return SimpleNamespace(
        symbol="BTCUSDT",
        rule_id="PRICE_PCT_5M_CRIT",
        rule_version="rv1",
        direction="UP",
        severity="CRITICAL",
        window_start=T0,
        window_end=T0 + dt.timedelta(minutes=5),
        detected_at=T0,
        revision=revision,
        revision_reason=revision_reason,
    )


def _interpretation(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "summary": "短线买盘推动价格突破前高。",
        "market_bias": "偏多",
        "scenario_probabilities": {"up": 0.5, "down": 0.2, "flat": 0.3},
        "dominant_factors": ["资金费率转正", "现货成交放量"],
        "invalidation_conditions": ["跌破 60000", "OI 连续两根下降"],
    }
    base.update(overrides)
    return base


def _texts(card: dict[str, Any]) -> str:
    return "\n".join(el["text"]["content"] for el in card["card"]["elements"] if "text" in el)


def test_revision_zero_has_no_interpretation_block() -> None:
    card = render_anomaly_card(_event(), body=RevisionBody.NONE, interpretation=None)

    assert card["card"]["header"]["title"]["content"] == "BTCUSDT 异动快报"
    assert len(card["card"]["elements"]) == 1
    assert "AI 解读" not in _texts(card)


def test_revision_one_full_renders_all_sections() -> None:
    card = render_anomaly_card(
        _event(revision=1, revision_reason="AI_INTERPRETATION_READY"),
        body=RevisionBody.FULL,
        interpretation=_interpretation(),
    )
    text = _texts(card)

    assert card["card"]["header"]["title"]["content"] == "BTCUSDT 异动快报（AI 解读已更新）"
    assert "**AI 摘要**：短线买盘推动价格突破前高。" in text
    assert "**市场偏向**：偏多" in text
    assert "上行 50% / 下行 20% / 震荡 30%" in text
    assert "主观估计，非统计胜率" in text
    assert "资金费率转正；现货成交放量" in text
    assert "跌破 60000；OI 连续两根下降" in text


def test_brief_marks_degraded_without_inventing_content() -> None:
    card = render_anomaly_card(
        _event(revision=1, revision_reason="DEGRADED_PARTIAL"),
        body=RevisionBody.BRIEF,
        interpretation={"degraded_reason": "LLM 超时"},
    )
    text = _texts(card)

    assert "分析降级（LLM 超时）" in text
    assert "AI 摘要" not in text
    assert "情景概率" not in text


def test_invalidation_conditions_capped() -> None:
    many = [f"条件{i}" for i in range(10)]
    card = render_anomaly_card(
        _event(revision=1),
        body=RevisionBody.FULL,
        interpretation=_interpretation(invalidation_conditions=many),
    )
    text = _texts(card)

    assert "条件2" in text
    assert "条件3" not in text


def test_long_summary_truncated() -> None:
    long = "长" * (SUMMARY_MAX_CHARS + 100)
    card = render_anomaly_card(
        _event(revision=1),
        body=RevisionBody.FULL,
        interpretation=_interpretation(summary=long),
    )
    text = _texts(card)

    assert "长" * (SUMMARY_MAX_CHARS - 1) + "…" in text
    assert "长" * (SUMMARY_MAX_CHARS + 1) not in text
