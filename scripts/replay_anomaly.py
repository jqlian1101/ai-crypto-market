"""异动规则回放：导出行情夹具并生成/校验基线（PRD 36.5）。

用法（从项目根目录运行）::

    # 从本地库导出一周 USDM_FUTURES K 线为录制夹具（需要 DATABASE_URL）
    python scripts/replay_anomaly.py export --start 2026-11-13 --end 2026-11-20

    # 基于夹具回放并写出基线（任何规则修改后须显式执行并在 PR 中说明差异）
    python scripts/replay_anomaly.py baseline

退出码：0 成功；1 失败。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from aicrypto.domain.anomaly.rules import PriceMoveThresholds
from aicrypto.services.anomaly_replay import Candle, ReplayReport, replay_symbol

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "market" / "replay_usdm_1w"
BASELINE_PATH = ROOT / "tests" / "fixtures" / "anomaly" / "baseline.json"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
TIMEFRAMES = ("5m", "15m", "1h")
DISABLED_RULES = ("PRICE_PCT_1M", "PRICE_PCT_1M_CRIT", "PRICE_GAP", "PRICE_FLASH_MOVE")
# 回放评估误报率时 WARN 触发后观察的时长（36.5：1h 后反向）。
REVERSAL_HORIZON = dt.timedelta(hours=1)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def fixture_path(symbol: str, timeframe: str, base: Path = FIXTURE_DIR) -> Path:
    return base / f"{symbol}_{timeframe}.json"


def load_fixture(symbol: str, timeframe: str, base: Path = FIXTURE_DIR) -> list[Candle]:
    rows = json.loads(fixture_path(symbol, timeframe, base).read_text(encoding="utf-8"))
    return [
        Candle(
            open_time=_parse_iso(r["open_time"]),
            close_time=_parse_iso(r["close_time"]),
            open=Decimal(r["open"]),
            high=Decimal(r["high"]),
            low=Decimal(r["low"]),
            close=Decimal(r["close"]),
            volume=Decimal(r["volume"]),
        )
        for r in rows
    ]


def export_fixtures(start: dt.datetime, end: dt.datetime) -> None:
    """从本地库导出已收盘 K 线。1h 周期向前多取 120 根作为 ATR/成交量预热。"""
    from sqlalchemy import select

    from aicrypto.infrastructure.database.models.market import MarketCandle
    from aicrypto.infrastructure.database.session import session_scope

    warmup = {"5m": dt.timedelta(hours=2), "15m": dt.timedelta(hours=2), "1h": dt.timedelta(0)}
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    with session_scope() as db:
        for symbol in SYMBOLS:
            for tf in TIMEFRAMES:
                rows = db.scalars(
                    select(MarketCandle)
                    .where(
                        MarketCandle.symbol == symbol,
                        MarketCandle.market_type == "USDM_FUTURES",
                        MarketCandle.exchange == "binance",
                        MarketCandle.timeframe == tf,
                        MarketCandle.is_closed.is_(True),
                        MarketCandle.open_time >= start - warmup[tf],
                        MarketCandle.close_time <= end,
                    )
                    .order_by(MarketCandle.open_time)
                ).all()
                payload = [
                    {
                        "open_time": _iso(r.open_time),
                        "close_time": _iso(r.close_time),
                        "open": str(r.open),
                        "high": str(r.high),
                        "low": str(r.low),
                        "close": str(r.close),
                        "volume": str(r.volume),
                    }
                    for r in rows
                ]
                path = fixture_path(symbol, tf)
                path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
                print(f"exported {path.relative_to(ROOT)}: {len(payload)} bars", flush=True)


def _reversal_rate(report: ReplayReport, closes_5m: list[Candle]) -> dict[str, Any]:
    """WARN 触发中「1h 后价格反向」的比例。只作指标输出，不做断言（36.5）。"""
    by_close = {c.close_time: c.close for c in closes_5m}
    total = 0
    reversed_count = 0
    for fire in report.fires:
        if fire.severity != "WARN" or not fire.rule_id.startswith("PRICE_PCT"):
            continue
        start_price = by_close.get(fire.fired_at)
        later = by_close.get(fire.fired_at + REVERSAL_HORIZON)
        if start_price is None or later is None:
            continue
        total += 1
        # 触发方向与后续 1h 变化相反即计为反向；触发方向以触发根的实体方向近似。
        trigger_bar = next(c for c in closes_5m if c.close_time == fire.fired_at)
        direction = 1 if trigger_bar.close >= trigger_bar.open else -1
        if (later - start_price) * direction < 0:
            reversed_count += 1
    return {
        "warn_price_samples": total,
        "warn_price_reversed_1h": reversed_count,
        "rate": round(reversed_count / total, 4) if total else None,
    }


def build_baseline(
    base: Path = FIXTURE_DIR,
    price_thresholds: PriceMoveThresholds | None = None,
) -> dict[str, Any]:
    """回放所有标的，返回基线结构（触发明细 + 汇总计数 + 抑制计数）。"""
    symbols: dict[str, Any] = {}
    for symbol in SYMBOLS:
        candles = {tf: load_fixture(symbol, tf, base) for tf in TIMEFRAMES}
        report = replay_symbol(
            symbol=symbol,
            candles_by_tf=candles,
            disabled_rules=DISABLED_RULES,
            price_thresholds=price_thresholds,
        )
        counts = Counter(f.rule_id for f in report.fires)
        symbols[symbol] = {
            "fire_counts": dict(sorted(counts.items())),
            "suppressed": {
                k: dict(sorted(v.items())) for k, v in sorted(report.suppressed.items())
            },
            "errors": report.error_count,
            "fires": [
                {"rule_id": f.rule_id, "severity": f.severity, "fired_at": _iso(f.fired_at)}
                for f in report.fires
            ],
            "false_positive_metric": _reversal_rate(report, candles["5m"]),
        }
    return {
        "schema_version": 1,
        "rule_version": "v1",
        "market_type": "USDM_FUTURES",
        "fixture": str(base.relative_to(ROOT)).replace("\\", "/"),
        "disabled_rules": list(DISABLED_RULES),
        "symbols": symbols,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    export = sub.add_parser("export")
    export.add_argument("--start", required=True)
    export.add_argument("--end", required=True)
    sub.add_parser("baseline")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "export":
            start = dt.datetime.fromisoformat(args.start).replace(tzinfo=dt.UTC)
            end = dt.datetime.fromisoformat(args.end).replace(tzinfo=dt.UTC)
            export_fixtures(start, end)
        else:
            baseline = build_baseline()
            BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
            BASELINE_PATH.write_text(
                json.dumps(baseline, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            for symbol, data in baseline["symbols"].items():
                print(f"{symbol}: {data['fire_counts']} suppressed={data['suppressed']}")
    except Exception as exc:
        print(f"replay_failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
