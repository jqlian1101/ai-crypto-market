"""衍生品异动规则纯函数（PRD 22.2.4）。

本模块实现 ``DER_*`` 规则族的**判定逻辑**，只做纯计算，不做 IO。
调用方（``services/anomaly_scan.py``）负责取数、落库与投递。

规则清单（PRD 22.2.4）：

===========================  ==================================  ========
规则 ID                       触发条件                             级别
===========================  ==================================  ========
``DER_OI_SURGE``             1h OI 变化率 ≥ +3.0%                 WARN
``DER_OI_SURGE_CRIT``        1h OI 变化率 ≥ +6.0%                 CRITICAL
``DER_OI_FLUSH``             1h OI 变化率 ≤ −3.0%                 CRITICAL
``DER_FUNDING_EXTREME``      |funding| ≥ 0.05%（8h 口径）           WARN
``DER_FUNDING_FLIP``         funding 穿越 0 零点                   INFO
``DER_BASIS_EXTREME``        |basis| 处于历史 95 分位以上          WARN
``DER_LS_SKEW``              多空比处于历史极端分位（≥P95 或 ≤P5）  WARN
``DER_PRICE_OI_DIVERGENCE``  价格与 OI 反向变动超过阈值           WARN
===========================  ==================================  ========

**缺失数据不得当作 0**：所有函数在输入缺失时返回 ``None`` 或空列表，
由调用方标记为 ``skipped``。把缺失当 0 会漏报（费率极端）或误报（OI 崩塌）。

**方向性**：``DER_OI_SURGE`` 只看**上升**（OI 激增伴随新仓位进场），
``DER_OI_FLUSH`` 只看**下降**（疑似强平/去杠杆）。两者方向相反，
不能用同一套「绝对值」判定。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from aicrypto.domain.anomaly.rules import AnomalyHit


@dataclass(frozen=True, slots=True)
class DerivativesThresholds:
    """衍生品规则阈值（PRD 22.2.4 初始值）。

    百分比类阈值以**百分数**为单位（3.0 表示 3%）；
    费率类阈值以**小数**为单位（0.0005 表示 0.05%）。
    """

    oi_change_warn_pct: float = 3.0
    oi_change_critical_pct: float = 6.0
    funding_abs_warn: float = 0.0005
    funding_abs_critical: float = 0.001
    divergence_pct: float = 1.0
    basis_percentile: float = 95.0
    ls_skew_percentile: float = 95.0


def evaluate_oi_change(
    *,
    oi_change_pct: float | None,
    thresholds: DerivativesThresholds | None = None,
) -> list[AnomalyHit]:
    """OI 激增 / 崩塌（``DER_OI_SURGE`` / ``DER_OI_FLUSH``）。

    ``oi_change_pct`` 是**百分数**（3.0 表示 +3%）。

    方向性判定（PRD 22.2.4）：

    - **激增**：变化率 ≥ ``oi_change_warn_pct``（+3%）；
      达到 ``oi_change_critical_pct``（+6%）升级为 CRITICAL；
    - **崩塌**：变化率 ≤ −``oi_change_warn_pct``（−3%）。
      PRD 规定崩塌一律 CRITICAL——去杠杆是需要立刻知道的事件。

    崩塌判定用 **WARN 阈值**而非 CRITICAL 阈值：−3% 就是崩塌信号，
    不必等到 −6%。

    缺失数据返回空列表（调用方标记 skipped）。
    """
    limits = thresholds or DerivativesThresholds()
    if oi_change_pct is None:
        return []

    hits: list[AnomalyHit] = []
    if oi_change_pct >= limits.oi_change_critical_pct:
        hits.append(
            AnomalyHit(
                rule_id="DER_OI_SURGE_CRIT",
                severity="CRITICAL",
                direction="UP",
                metrics={"oi_change_pct": round(oi_change_pct, 6)},
                threshold={"oi_change_pct": limits.oi_change_critical_pct},
            )
        )
    elif oi_change_pct >= limits.oi_change_warn_pct:
        hits.append(
            AnomalyHit(
                rule_id="DER_OI_SURGE",
                severity="WARN",
                direction="UP",
                metrics={"oi_change_pct": round(oi_change_pct, 6)},
                threshold={"oi_change_pct": limits.oi_change_warn_pct},
            )
        )
    elif oi_change_pct <= -limits.oi_change_warn_pct:
        hits.append(
            AnomalyHit(
                rule_id="DER_OI_FLUSH",
                severity="CRITICAL",
                direction="DOWN",
                metrics={"oi_change_pct": round(oi_change_pct, 6)},
                threshold={"oi_change_pct": limits.oi_change_warn_pct},
            )
        )
    return hits


def evaluate_funding_extreme(
    *,
    funding_rate: Decimal | float | None,
    thresholds: DerivativesThresholds | None = None,
) -> AnomalyHit | None:
    """资金费率极端（``DER_FUNDING_EXTREME``）。

    ``funding_rate`` 是**小数**（0.0005 表示 0.05%）。
    判定用**绝对值**——正负极端同样值得注意。

    缺失返回 ``None``（不得当作 0，0 是「正常费率」）。
    """
    limits = thresholds or DerivativesThresholds()
    if funding_rate is None:
        return None
    value = float(funding_rate)
    magnitude = abs(value)
    if magnitude >= limits.funding_abs_critical:
        severity = "CRITICAL"
        threshold = limits.funding_abs_critical
    elif magnitude >= limits.funding_abs_warn:
        severity = "WARN"
        threshold = limits.funding_abs_warn
    else:
        return None
    return AnomalyHit(
        # CRITICAL 变体带 ``_CRIT`` 后缀，与 ``PRICE_PCT_*`` / ``VOL_*``
        # 等规则族的约定一致——规则版本表按 rule_id 建行，
        # 两种级别必须是两个不同的 rule_id。
        rule_id="DER_FUNDING_EXTREME" + ("_CRIT" if severity == "CRITICAL" else ""),
        severity=severity,
        direction="UP" if value > 0 else "DOWN",
        metrics={
            "funding_rate": round(value, 8),
            "abs_funding_rate": round(magnitude, 8),
        },
        threshold={"abs_funding_rate": threshold},
    )


def evaluate_funding_flip(
    *,
    funding_rate: Decimal | float | None,
    previous_rate: Decimal | float | None,
) -> AnomalyHit | None:
    """资金费率翻号（``DER_FUNDING_FLIP``，INFO）。

    判定：当前值与上一期的**符号相反**，且两者都非零。

    「上一期」由调用方给出（已结算的历史费率）。
    缺失任一值返回 ``None``——无法判断是否翻号，
    不能因为「当前是 0」就判定为翻号。
    """
    if funding_rate is None or previous_rate is None:
        return None
    current = float(funding_rate)
    previous = float(previous_rate)
    if current == 0.0 or previous == 0.0:
        # 一方恰为 0：符号未真正穿越零点，不算翻号。
        return None
    if (current > 0) == (previous > 0):
        return None
    return AnomalyHit(
        rule_id="DER_FUNDING_FLIP",
        severity="INFO",
        direction="UP" if current > 0 else "DOWN",
        metrics={
            "funding_rate": round(current, 8),
            "previous_funding_rate": round(previous, 8),
        },
        threshold={"funding_rate": 0.0},
    )


def evaluate_basis_extreme(
    *,
    basis_pct: float | None,
    percentile: float | None,
    thresholds: DerivativesThresholds | None = None,
) -> AnomalyHit | None:
    """基差异常（``DER_BASIS_EXTREME``）。

    ``basis_pct`` 是当前基差（**小数**，0.0012 表示 0.12%）。
    ``percentile`` 是当前基差在历史样本中的分位（0–1）。

    判定：``|basis|`` 处于历史极端分位（默认 ≥P95）。
    PRD 说的是「|basis| 处于历史 95 分位以上」——
    判定用的是**绝对值的分位**，因此传入的 percentile 必须
    已经是「绝对基差」的分位，由调用方保证。

    分位不可得（样本不足）返回 ``None``——不能用 0 冒充。
    """
    limits = thresholds or DerivativesThresholds()
    if basis_pct is None or percentile is None:
        return None
    limit = limits.basis_percentile / 100.0
    if percentile < limit:
        return None
    value = float(basis_pct)
    return AnomalyHit(
        rule_id="DER_BASIS_EXTREME",
        severity="WARN",
        direction="UP" if value > 0 else "DOWN",
        metrics={
            "basis_pct": round(value, 8),
            "basis_percentile": round(percentile, 6),
        },
        threshold={"basis_percentile": limits.basis_percentile},
    )


def evaluate_ls_skew(
    *,
    skew_percentile: float | None,
    thresholds: DerivativesThresholds | None = None,
) -> AnomalyHit | None:
    """多空拥挤（``DER_LS_SKEW``）。

    ``skew_percentile`` 是多空比的历史分位（0–1）。
    判定：处于极端分位——≥P95（多头拥挤）或 ≤P5（空头拥挤）。

    PRD 22.2.4 规定 WARN 级。多空比本身不可直接观测，
    这里用「资金费率 + 持仓量 + 价格」的联合方向做保守推断，
    缺失时返回 ``None``。
    """
    limits = thresholds or DerivativesThresholds()
    if skew_percentile is None:
        return None
    high = limits.ls_skew_percentile / 100.0
    low = (100.0 - limits.ls_skew_percentile) / 100.0
    if skew_percentile >= high:
        direction = "UP"
    elif skew_percentile <= low:
        direction = "DOWN"
    else:
        return None
    return AnomalyHit(
        rule_id="DER_LS_SKEW",
        severity="WARN",
        direction=direction,
        metrics={"skew_percentile": round(skew_percentile, 6)},
        threshold={"ls_skew_percentile": limits.ls_skew_percentile},
    )


def evaluate_price_oi_divergence(
    *,
    price_change_pct: float | None,
    oi_change_pct: float | None,
    thresholds: DerivativesThresholds | None = None,
) -> AnomalyHit | None:
    """价格与持仓背离（``DER_PRICE_OI_DIVERGENCE``）。

    两个变化率都是**百分数**。判定：方向相反且两者幅度都超过阈值。

    - 价格涨 + OI 跌 → 疑似平仓推动（多头离场）；
    - 价格跌 + OI 涨 → 疑似新空进场。

    任一缺失返回 ``None``。
    """
    limits = thresholds or DerivativesThresholds()
    if price_change_pct is None or oi_change_pct is None:
        return None
    threshold = limits.divergence_pct
    opposing = (price_change_pct > threshold and oi_change_pct < -threshold) or (
        price_change_pct < -threshold and oi_change_pct > threshold
    )
    if not opposing:
        return None
    return AnomalyHit(
        rule_id="DER_PRICE_OI_DIVERGENCE",
        severity="WARN",
        direction="UP" if price_change_pct > 0 else "DOWN",
        metrics={
            "price_change_pct": round(price_change_pct, 6),
            "oi_change_pct": round(oi_change_pct, 6),
        },
        threshold={"divergence_pct": threshold},
    )


__all__ = [
    "DerivativesThresholds",
    "evaluate_basis_extreme",
    "evaluate_funding_extreme",
    "evaluate_funding_flip",
    "evaluate_ls_skew",
    "evaluate_oi_change",
    "evaluate_price_oi_divergence",
]
