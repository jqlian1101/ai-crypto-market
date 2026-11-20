"""去重与冷却的纯判定逻辑（PRD 22.4.4 / 22.4.4.1）。

两层的语义与键空间严格分离：

- 去重层：唯一键 ``(fingerprint, revision)``，TTL 默认 24h，只允许成功投递一次；
- 冷却层：唯一键 ``cooldown_key``（symbol + rule_id），CRITICAL 默认 5 分钟，
  WARN 默认 30 分钟；升级投递（revision > 0）不占用冷却窗口。

本模块只做判定，不读写存储。存储由适配层注入时间戳与已见集合。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

DEDUP_TTL = dt.timedelta(hours=24)
COOLDOWN_CRITICAL = dt.timedelta(minutes=5)
COOLDOWN_WARN = dt.timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class DedupDecision:
    allowed: bool
    reason: str


def dedup_key(fingerprint: str, revision: int) -> str:
    """去重键。与冷却键空间独立，不得复用。"""
    return f"dedup:{fingerprint}:{revision}"


def cooldown_window(severity: str) -> dt.timedelta:
    if severity == "CRITICAL":
        return COOLDOWN_CRITICAL
    if severity == "WARN":
        return COOLDOWN_WARN
    raise ValueError(f"不支持的 severity 冷却：{severity}")


def decide_delivery(
    *,
    fingerprint: str,
    revision: int,
    severity: str,
    cooldown_key: str,
    now: dt.datetime,
    delivered_keys: set[str],
    last_fired_at: dict[str, dt.datetime],
) -> DedupDecision:
    """判定一次投递是否放行。

    顺序：先去重（同 fingerprint+revision 只投一次），再冷却（仅首投检查）。
    """
    if dedup_key(fingerprint, revision) in delivered_keys:
        return DedupDecision(False, "DEDUP_HIT")

    # 升级投递（revision > 0）不占用冷却窗口（22.4.4.1 第 3 条）。
    if revision == 0:
        last = last_fired_at.get(cooldown_key)
        if last is not None and now - last < cooldown_window(severity):
            return DedupDecision(False, "COOLDOWN")

    return DedupDecision(True, "OK")


__all__ = [
    "COOLDOWN_CRITICAL",
    "COOLDOWN_WARN",
    "DEDUP_TTL",
    "DedupDecision",
    "cooldown_window",
    "decide_delivery",
    "dedup_key",
]
