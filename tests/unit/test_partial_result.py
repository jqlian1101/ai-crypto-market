"""部分结果构造的单元测试（PRD 24.4）。

**这里防的是什么**：部分结果一旦开始编造结论，问题比「整体失败」
严重得多——一份看起来完整、实际有一半是编的报告，会进入预测追踪、
评估统计和通知推送，后面所有环节都无法再分辨它是编的。

所以这些测试锁死三条红线：

1. **不得出现方向判断**（``market_bias`` 必须是「没有判断」而不是 "NEUTRAL"）；
2. **不得出现情景/概率/分歧**（模型没跑完，这些结论不存在）；
3. **证据必须指向快照里真实存在、且值一致**（可被程序校验）。
"""

from __future__ import annotations

import datetime as dt

import pytest

from aicrypto.services.partial_result import (
    DEGRADED_REASON_TIMEOUT,
    PARTIAL_BIAS,
    build_partial_content,
)


@pytest.fixture
def snapshot() -> dict:
    """一份典型的快照内容（结构与 ``build_snapshot`` 的返回一致）。"""
    return {
        "snapshot_id": "snap_abc",
        "indicators": {
            "5m": {
                "bars_used": 300,
                "rsi_14": 55.0,
                "sr_zones": [{"low": 100.0, "high": 102.0, "touches": 3, "kind": "support"}],
            },
            "15m": {
                "bars_used": 300,
                "rsi_14": 48.0,
                "sr_zones": [{"low": 98.0, "high": 101.0, "touches": 2, "kind": "resistance"}],
            },
            # 样本不足：其指标无意义，**不得**被引用
            "1h": {"bars_used": 40, "status": "INSUFFICIENT"},
            "4h": {"bars_used": 300, "sr_zones": []},
        },
        "market_structure": {
            "5m": {"bias": "UP", "last_swing_high": 105.0},
            "15m": {"bias": "DOWN", "last_swing_low": 95.0},
        },
        "market_regime": {
            "5m": {"state": "RANGE"},
            "15m": {"state": "UPTREND"},
            "4h": {"state": "HIGH_VOLATILITY"},
        },
    }


def _build(snapshot: dict, **kwargs):
    params = {
        "snapshot_id": "snap_abc",
        "failure_reason": "模型调用超时",
        "error_code": "LLM_TIMEOUT",
        "timeout": True,
    }
    params.update(kwargs)
    return build_partial_content(snapshot, **params)


class TestNoFabricatedConclusion:
    """红线一：不得出现任何「模型才能得出」的结论。"""

    def test_market_bias_is_not_a_real_conclusion(self, snapshot: dict) -> None:
        """``market_bias`` 必须是「没有判断」，**绝不能是 "NEUTRAL"**。

        填 "NEUTRAL" 看起来无害，但在下游是个**有效结论**：
        前端显示「主要判断：NEUTRAL」，评估器把它当成
        「模型判断为中性」计入准确率统计——
        而模型根本没给出判断。把「不知道」写成「中性」，
        是在用假数据污染评估口径。
        """
        content = _build(snapshot)
        assert content.market_bias == PARTIAL_BIAS
        assert content.market_bias != "NEUTRAL"
        assert content.market_bias != "BULLISH"
        assert content.market_bias != "BEARISH"

    def test_no_scenarios_or_probabilities(self, snapshot: dict) -> None:
        """情景与概率是模型推演出来的，模型没跑完就没有。"""
        content = _build(snapshot)
        assert content.scenarios == []
        assert content.scenario_probabilities is None

    def test_no_disagreements_or_counter_evidence(self, snapshot: dict) -> None:
        """分歧与反证都是「模型对事实的解读」，不得凭空生成。"""
        content = _build(snapshot)
        assert content.disagreements == []
        assert content.counter_evidence == []
        assert content.dominant_factors == []

    def test_no_watch_or_invalidation_conditions(self, snapshot: dict) -> None:
        """观察条件与失效条件属于结论的一部分，同样不得编造。"""
        content = _build(snapshot)
        assert content.watch_conditions == []
        assert content.invalidation_conditions == []
        assert content.prediction_ids == []

    def test_summary_states_failure_not_a_conclusion(self, snapshot: dict) -> None:
        """摘要必须说明「未完成」，而不是给个看似正常的结论。"""
        content = _build(snapshot)
        assert "未完成" in content.summary
        assert "超时" in content.summary
        # 摘要里必须声明不包含方向判断
        assert "不包含方向判断" in content.summary

    def test_uncertainties_explain_what_is_missing(self, snapshot: dict) -> None:
        """不确定性必须**明确说出缺了什么**。"""
        content = _build(snapshot)
        joined = " ".join(content.uncertainties)
        assert "market_bias" in joined
        assert "情景概率" in joined
        assert "LLM_TIMEOUT" in joined


class TestCompletedFacts:
    """红线二：确定性的已完成部分必须真的被交出来。"""

    def test_regime_states_are_included(self, snapshot: dict) -> None:
        """regime 是程序算出来的状态，属于「已完成部分」。"""
        content = _build(snapshot)
        assert content.timeframe_views == {
            "5m": "RANGE",
            "15m": "UPTREND",
            "4h": "HIGH_VOLATILITY",
        }

    def test_support_resistance_levels_are_included(self, snapshot: dict) -> None:
        """支撑阻力位是算出来的客观价位，可以安全交付。"""
        content = _build(snapshot)
        assert any("100.0 ~ 102.0" in item for item in content.key_levels)
        assert any("98.0 ~ 101.0" in item for item in content.key_levels)

    def test_insufficient_bars_are_not_referenced(self, snapshot: dict) -> None:
        """**样本不足的周期不得被引用**——那会引用到一个空值。"""
        content = _build(snapshot)
        paths = {ev.source_path for ev in content.evidence}
        assert not any("1h" in path for path in paths)

    def test_evidence_paths_resolve_and_match(self, snapshot: dict) -> None:
        """证据路径必须能在快照里解析出来，且值一致。

        这对应 ``create_report`` 的证据校验：
        路径解析不了会抛 ``LLM_SCHEMA_INVALID``，
        值不一致会抛 ``EVAL_LOOKAHEAD_VIOLATION``。
        """
        content = _build(snapshot)
        assert content.evidence
        for ev in content.evidence:
            value: object = snapshot
            for segment in ev.source_path.split("."):
                value = value[segment]  # type: ignore[index]
            assert str(value) == str(ev.value)
            assert ev.snapshot_id == "snap_abc"

    def test_evidence_only_references_program_computed_facts(self, snapshot: dict) -> None:
        """证据只应引用**程序算出来的**指标，不引用 K 线序列。"""
        content = _build(snapshot)
        for ev in content.evidence:
            assert not ev.source_path.startswith("multi_timeframe_ohlcv")


class TestDegradation:
    """红线三：降级信息必须显式，不得让读者误以为是正常结论。"""

    def test_timeout_uses_timeout_reason(self, snapshot: dict) -> None:
        assert DEGRADED_REASON_TIMEOUT == "TIMEOUT"
        content = _build(snapshot, timeout=True)
        assert "超时" in content.summary

    def test_failure_is_distinguished_from_timeout(self, snapshot: dict) -> None:
        content = _build(
            snapshot,
            timeout=False,
            error_code="LLM_UNAVAILABLE",
            failure_reason="连接被重置",
        )
        assert "模型调用失败" in content.summary
        assert "超时" not in content.summary

    def test_failure_reason_is_propagated(self, snapshot: dict) -> None:
        content = _build(snapshot, failure_reason="连接被重置")
        assert "连接被重置" in content.summary
        assert any("连接被重置" in u for u in content.uncertainties)


class TestRobustness:
    """异常输入不能把构造过程搞崩——崩了就退回「整体失败」了。"""

    def test_empty_snapshot_yields_minimal_but_valid_content(self) -> None:
        content = build_partial_content(
            {},
            snapshot_id="snap_x",
            failure_reason="超时",
            timeout=True,
        )
        assert content.market_bias == PARTIAL_BIAS
        assert content.evidence == []
        assert content.key_levels == []
        assert content.timeframe_views == {}

    def test_malformed_blocks_are_ignored(self) -> None:
        """结构不对的块要跳过，而不是抛异常。"""
        content = build_partial_content(
            {
                "indicators": "not-a-dict",
                "market_regime": {"5m": "not-a-dict"},
                "market_structure": {"5m": 123},
            },
            snapshot_id="snap_x",
            failure_reason="超时",
            timeout=True,
        )
        assert content.evidence == []
        assert content.timeframe_views == {}

    def test_missing_snapshot_id_evidence_is_skipped(self) -> None:
        """缺 state / bias 的块不产出证据。"""
        content = build_partial_content(
            {
                "market_regime": {"5m": {}},
                "market_structure": {"5m": {"bias": None}},
            },
            snapshot_id="snap_x",
            failure_reason="超时",
            timeout=True,
        )
        assert content.evidence == []

    def test_as_of_is_optional(self, snapshot: dict) -> None:
        content = _build(snapshot, as_of=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
        assert content.market_bias == PARTIAL_BIAS
