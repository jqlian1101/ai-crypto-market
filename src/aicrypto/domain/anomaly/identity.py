"""异动事件身份、指纹与冷却键（PRD 22.3 / 22.4.4 / 22.4.4.1）。

约束：

- ``fingerprint`` 只标识事件身份，不得包含 ``revision`` 或会随分析结果变化的内容；
- 去重键为 ``(fingerprint, revision)``；
- 冷却键为 ``symbol + rule_id``，与去重键空间独立（不得共用同一键）。
"""

from __future__ import annotations

import datetime as dt
import hashlib

FINGERPRINT_PREFIX = "ae_"


def build_fingerprint(
    *,
    symbol: str,
    market_type: str,
    rule_id: str,
    window_start: dt.datetime,
) -> str:
    """事件身份哈希：``symbol + market_type + rule_id + window_start``。

    同一规则、同一窗口重复检出得到相同指纹（幂等）；输入不含 revision 与 AI 结论。
    """
    if window_start.tzinfo is None:
        raise ValueError("window_start 必须带时区（统一存 UTC）")
    anchor = window_start.astimezone(dt.UTC).isoformat()
    raw = "|".join((symbol, market_type, rule_id, anchor))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{FINGERPRINT_PREFIX}{digest}"


def build_cooldown_key(*, symbol: str, rule_id: str) -> str:
    """冷却键：``symbol:rule_id``（22.3 示例）。"""
    return f"{symbol}:{rule_id}"


def build_event_id(fingerprint: str, revision: int = 0) -> str:
    """事件主键。由 fingerprint 派生，同一事件在多 revision 下主键不冲突。"""
    if revision < 0:
        raise ValueError("revision 不得为负")
    return f"{fingerprint}_r{revision}"
