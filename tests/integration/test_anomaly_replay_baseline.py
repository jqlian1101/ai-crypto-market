"""异动规则回放基线测试（PRD 36.5）。

断言：

- 固定夹具回放结果与 ``tests/fixtures/anomaly/baseline.json`` 完全一致；
- 修改阈值后基线必须变化（证明规则确实读取配置）；
- 误报率只作为指标输出，不设硬断言。

基线变化时，须运行 ``python scripts/replay_anomaly.py baseline`` 并在 PR 中说明差异。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from aicrypto.domain.anomaly.rules import PriceMoveThresholds

ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = ROOT / "tests" / "fixtures" / "anomaly" / "baseline.json"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "market" / "replay_usdm_1w"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "replay_anomaly", ROOT / "scripts" / "replay_anomaly.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _comparable(baseline: dict) -> dict:
    """去掉指标字段，只比较规则行为（触发、级别、抑制、错误）。"""
    out = json.loads(json.dumps(baseline))
    for sym in out["symbols"].values():
        sym.pop("false_positive_metric", None)
    return out


def test_replay_matches_committed_baseline() -> None:
    script = _load_script()
    committed = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    current = script.build_baseline(FIXTURE_DIR)
    assert _comparable(current) == _comparable(committed)


def test_threshold_change_changes_baseline() -> None:
    script = _load_script()
    committed = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    # 把 5m WARN 阈值从 0.60% 提高到 1.00%，触发明细必须随之变化
    stricter = PriceMoveThresholds(
        warn_pct={"5m": 1.00, "15m": 1.20, "1h": 2.50},
        critical_pct={"5m": 1.20, "15m": 2.50, "1h": 4.50},
    )
    changed = script.build_baseline(FIXTURE_DIR, price_thresholds=stricter)
    assert _comparable(changed) != _comparable(committed)
