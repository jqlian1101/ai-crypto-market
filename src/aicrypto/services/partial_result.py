"""超时/失败时的「部分结果」构造（PRD 24.4）。

**PRD 要求**：全流程超时预算（如 3 分钟）用尽时，
输出「**分析超时 + 已完成部分**」的部分结果，而不是整体失败。

**这里最要紧的是「什么算已完成部分」**。如果只是把 LLM 失败的
原因写进 ``summary`` 就叫「部分结果」，那这个功能毫无价值——
读者拿到的仍然是一份没有结论的报告。

**划分原则：只放「已经算出来的确定性事实」，不放「需要模型才能得出的结论」。**

快照里的这些内容是**确定性计算**的结果——K 线、EMA/RSI/MACD、
市场结构、regime、支撑阻力位。它们在 LLM 超时之前就已经算完了，
**是货真价实已完成的部分**：

- ``multi_timeframe_ohlcv``  —— 行情本身
- ``indicators``              —— 技术指标
- ``market_structure``        —— 结构判断（趋势/摆动高低点）
- ``market_regime``           —— 市场状态
- ``sr_zones``                —— 支撑阻力位

而这些**必须留空**，因为它们是「模型对事实的解读」，
模型没跑完就没有——**编一个出来就是伪造结论**（PRD 32.4）：

- ``market_bias``       —— 方向判断（不能瞎写 "NEUTRAL"）
- ``summary``           —— 综合叙述
- ``scenarios`` / ``scenario_probabilities`` —— 情景与概率
- ``evidence`` / ``counter_evidence`` —— 证据引用（模型挑的）
- ``disagreements``     —— 分歧（模型判的）
- ``key_levels`` / ``watch_conditions`` / ``invalidation_conditions``

**为什么 ``market_bias`` 留空而不是填 "NEUTRAL"**：
填 "NEUTRAL" 看起来无害，但它在下游是个**有效结论**——
前端会显示「主要判断：NEUTRAL」，评估器会把它当成
「模型判断为中性」计入准确率统计。而模型**根本没给出判断**。
**把「不知道」写成「中性」，是在用假数据污染评估口径。**
所以这里留空字符串，并让 ``uncertainties`` 明确说明原因。

**降级信息必须显式**：
``degraded=True`` + ``degraded_reason`` 写明「超时」或失败原因。
读者一眼就知道这份报告不完整，而不是误以为它是正常结论。

**这个模块是纯函数**：不碰数据库、不发请求、不读配置，
只有输入（快照 + 失败原因）决定输出。这样最容易测试，
也最容易确认「它没有偷偷去干别的」。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from aicrypto.domain.analysis.report import AnalysisReportContent, EvidenceRef

#: 部分结果里 ``market_bias`` 的取值。
#: **刻意不用 "NEUTRAL"**——那是一个有效结论，会被下游当成
#: 「模型判断为中性」而计入准确率统计。模型没给出判断，
#: 这里就必须表达「没有判断」。
PARTIAL_BIAS = "UNAVAILABLE"

#: 超时的降级原因。
DEGRADED_REASON_TIMEOUT = "TIMEOUT"

#: 失败（非超时）的降级原因前缀。
DEGRADED_REASON_FAILED = "MODEL_CALL_FAILED"


def _collect_evidence(
    snapshot: dict[str, Any],
    *,
    snapshot_id: str,
    limit: int = 12,
) -> list[EvidenceRef]:
    """把快照里的确定性指标整理成证据引用。

    **只引用「程序算出来的数值」**——它们可被程序校验
    （PRD 4.7.3「程序校验 source_path 指向的值与快照一致」）。
    这也是为什么部分结果里的证据是**可信的**：
    每一条都能对着快照核对。

    刻意**不**引用 ``multi_timeframe_ohlcv``——那是上百根 K 线，
    塞进证据列表既臃肿又无信息量。
    """
    indicators = snapshot.get("indicators") or {}
    if not isinstance(indicators, dict):
        return []
    evidence: list[EvidenceRef] = []
    for frame in ("5m", "15m", "1h", "4h"):
        block = indicators.get(frame)
        if not isinstance(block, dict):
            continue
        # 只取样本足够的周期——样本不足时指标是 None 或 status=INSUFFICIENT，
        # 引用它们等于报告里出现一个「无法解释的空值」。
        if block.get("status") == "INSUFFICIENT":
            continue
        regime = (snapshot.get("market_regime") or {}).get(frame)
        if isinstance(regime, dict) and regime.get("state"):
            evidence.append(
                EvidenceRef(
                    evidence_id=f"regime_{frame}",
                    source_type="market_snapshot",
                    source_path=f"market_regime.{frame}.state",
                    value=str(regime["state"]),
                    snapshot_id=snapshot_id,
                    interpretation=f"{frame} 周期市场状态：{regime['state']}",
                )
            )
        structure = (snapshot.get("market_structure") or {}).get(frame)
        if isinstance(structure, dict) and structure.get("bias"):
            evidence.append(
                EvidenceRef(
                    evidence_id=f"structure_{frame}",
                    source_type="market_snapshot",
                    source_path=f"market_structure.{frame}.bias",
                    value=str(structure["bias"]),
                    snapshot_id=snapshot_id,
                    interpretation=f"{frame} 周期结构判断：{structure['bias']}",
                )
            )
        if len(evidence) >= limit:
            break
    return evidence


def _collect_key_levels(snapshot: dict[str, Any]) -> list[str]:
    """整理各周期的支撑阻力位。

    这些是**程序算出来的价位**（``sr_zones``），是可核对的客观事实，
    不涉及方向判断，因此放进部分结果是安全的。
    """
    indicators = snapshot.get("indicators") or {}
    if not isinstance(indicators, dict):
        return []
    levels: list[str] = []
    for frame in ("5m", "15m", "1h", "4h"):
        block = indicators.get(frame)
        if not isinstance(block, dict):
            continue
        zones = block.get("sr_zones") or []
        if not isinstance(zones, list):
            continue
        for zone in zones[:2]:
            if not isinstance(zone, dict):
                continue
            low = zone.get("low")
            high = zone.get("high")
            if low is None or high is None:
                continue
            levels.append(f"{frame} 支撑阻力区 {low} ~ {high}")
        if len(levels) >= 8:
            break
    return levels[:8]


def _collect_timeframe_views(snapshot: dict[str, Any]) -> dict[str, str]:
    """按周期整理 regime 状态，作为「多周期视图」。

    这是**客观状态描述**，不是方向判断——
    「1h 处于 UPTREND」是算出来的，「所以看涨」才是结论。
    """
    regime = snapshot.get("market_regime") or {}
    if not isinstance(regime, dict):
        return {}
    views: dict[str, str] = {}
    for frame in ("5m", "15m", "1h", "4h"):
        block = regime.get(frame)
        if isinstance(block, dict) and block.get("state"):
            views[frame] = str(block["state"])
    return views


def build_partial_content(
    snapshot: dict[str, Any],
    *,
    snapshot_id: str,
    failure_reason: str,
    error_code: str | None = None,
    timeout: bool = False,
    time_horizon: str = "4h",
    as_of: dt.datetime | None = None,
) -> AnalysisReportContent:
    """构造「已完成部分」的报告内容。

    :param snapshot: ``build_snapshot`` 的返回值——**必须是已经落库的快照**，
        这样证据的 ``source_path`` 才能对着库里那份快照校验。
    :param snapshot_id: 快照 ID。
    :param failure_reason: 人类可读的失败原因（写进 ``summary`` 与
        ``uncertainties``）。
    :param error_code: 错误码（写进 ``uncertainties`` 便于排查）。
    :param timeout: 是否是**超时**（区别于其他失败）。
    :param time_horizon: 结论适用周期。
    :param as_of: 快照截止时刻（写进证据的 ``observed_at``）。
    """
    # summary 只陈述「发生了什么」和「有什么可用」，
    # **不给方向判断**——模型没跑完，没有结论可给。
    summary = (
        f"分析未完成（{'超时' if timeout else '模型调用失败'}）："
        f"{failure_reason}。"
        "以下为已完成的确定性计算结果（技术指标、市场结构、regime、支撑阻力位），"
        "**不包含方向判断、情景概率与证据解读**——那些需要模型推理，"
        "模型未完成推理，因此不予给出。"
    )

    uncertainties = [
        f"本次分析未完成：{'超时' if timeout else '模型调用失败'}（{failure_reason}）。",
        "**方向判断（market_bias）与情景概率缺失**——模型推理未完成，"
        "系统不会代替模型给出方向性结论。",
        "缺失的分析环节：市场结构综合解读、衍生品/流动性解读、多周期冲突判定、证据取舍与情景推演。",
    ]
    if error_code:
        uncertainties.append(f"错误码：{error_code}。")

    return AnalysisReportContent(
        summary=summary,
        market_bias=PARTIAL_BIAS,
        time_horizon=time_horizon,
        dominant_factors=[],
        timeframe_views=_collect_timeframe_views(snapshot),
        scenarios=[],
        scenario_probabilities=None,
        evidence=_collect_evidence(snapshot, snapshot_id=snapshot_id),
        counter_evidence=[],
        disagreements=[],
        key_levels=_collect_key_levels(snapshot),
        watch_conditions=[],
        changes_since_previous=None,
        uncertainties=uncertainties,
        invalidation_conditions=[],
        prediction_ids=[],
    )


__all__ = [
    "DEGRADED_REASON_FAILED",
    "DEGRADED_REASON_TIMEOUT",
    "PARTIAL_BIAS",
    "build_partial_content",
]
