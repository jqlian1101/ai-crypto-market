"""规则种子必须覆盖引擎可能输出的全部 rule_id（否则 anomaly_events 外键违例）。"""

from __future__ import annotations

from aicrypto.services.anomaly_scan import _V1_RULES

# 引擎可能输出的基础 ID；每个都有 WARN 与 _CRIT 两种形态。
ENGINE_BASE_IDS = (
    "PRICE_PCT_5M",
    "PRICE_PCT_15M",
    "PRICE_PCT_1H",
    "VOL_ATR_SPIKE",
    "VOL_REL_VOLUME",
)


def test_seed_covers_every_engine_output_with_both_severities() -> None:
    seeded = {r["rule_id"] for r in _V1_RULES}
    expected = set(ENGINE_BASE_IDS) | {f"{base}_CRIT" for base in ENGINE_BASE_IDS}
    assert seeded == expected


def test_seed_severity_matches_rule_id_suffix() -> None:
    for rule in _V1_RULES:
        is_crit = rule["rule_id"].endswith("_CRIT")
        assert (rule["severity"] == "CRITICAL") == is_crit, rule["rule_id"]
