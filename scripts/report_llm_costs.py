#!/usr/bin/env python
"""LLM 成本报表（只读）。

用法::

    python scripts/report_llm_costs.py
    python scripts/report_llm_costs.py --days 7
    python scripts/report_llm_costs.py --since 2026-11-01 --until 2026-12-01
    python scripts/report_llm_costs.py --json

**什么时候需要跑**：
- 决定「多 Agent 拆分是否值得」**之前**（调用量会成倍上升）；
- 改 Prompt 之后（看哪版最烧 token）；
- 定期对账（费用是不是与供应商账单一致）。

**读数纪律**：报表若提示「存在未定价调用」，
那么费用数字是**下界**而非实际支出——
单价缺失的行在库里记的是 0（该列 NOT NULL），
直接汇总会把「没登记单价」读成「不花钱」。
处置方式是给这些模型补 ``model_pricing`` 行
（本地模型登记 0 价即可）。

退出码：0 正常；1 参数非法或取数失败。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict
from typing import Any

from aicrypto.common.clock import now
from aicrypto.config.settings import get_settings
from aicrypto.infrastructure.database.session import session_scope
from aicrypto.infrastructure.logging import configure_logging, get_logger
from aicrypto.services.cost_report import (
    DEFAULT_WINDOW_DAYS,
    CostBucket,
    CostReport,
    build_cost_report,
    fetch_cost_rows,
    render_cost_report,
)

logger = get_logger(__name__)


def _parse_date(value: str) -> dt.datetime:
    """解析 ``YYYY-MM-DD``（UTC 零点）或完整 ISO 时间。

    只接受**不带时区**的输入并按 UTC 解释——
    成本数据统一存 UTC（PRD 30.1），本地时区会让「按日分组」错位。
    """
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"时间格式非法：{value!r}（应为 YYYY-MM-DD 或 ISO-8601）") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _bucket_to_dict(bucket: CostBucket) -> dict[str, Any]:
    return {
        "key": bucket.key,
        "calls": bucket.calls,
        "input_tokens": bucket.input_tokens,
        "output_tokens": bucket.output_tokens,
        "total_tokens": bucket.total_tokens,
        "cost_usd": str(bucket.cost_usd),
        "unpriced_calls": bucket.unpriced_calls,
    }


def _report_to_dict(report: CostReport) -> dict[str, Any]:
    """把报表转成 JSON 可序列化的 dict。

    ``Decimal`` 一律转成**字符串**——
    ``json.dumps`` 默认会把 ``Decimal`` 当 float 处理，
    金额用浮点表达会引入表示误差（30.1 的 MoneyType 约定）。
    """
    return {
        "since": report.since.isoformat(),
        "until": report.until.isoformat(),
        "has_unpriced": report.has_unpriced,
        "cost_is_lower_bound": report.is_lower_bound,
        "total": _bucket_to_dict(report.total),
        "by_model": [_bucket_to_dict(b) for b in report.by_model],
        "by_tier": [_bucket_to_dict(b) for b in report.by_tier],
        "by_agent": [_bucket_to_dict(b) for b in report.by_agent],
        "by_prompt_version": [_bucket_to_dict(b) for b in report.by_prompt_version],
        "by_status": [_bucket_to_dict(b) for b in report.by_status],
        "by_day": [_bucket_to_dict(b) for b in report.by_day],
        "unpriced_models": [asdict(m) for m in report.unpriced_models],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 LLM 成本报表（只读）")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=f"回看天数（默认 {DEFAULT_WINDOW_DAYS}）；与 --since/--until 互斥",
    )
    parser.add_argument("--since", help="起始时间（UTC），如 2026-11-01")
    parser.add_argument("--until", help="结束时间（UTC），如 2026-12-01")
    parser.add_argument("--json", action="store_true", help="输出 JSON（便于接入其他工具）")
    args = parser.parse_args(argv)

    if args.since and args.until:
        try:
            since = _parse_date(args.since)
            until = _parse_date(args.until)
        except ValueError as exc:
            print(f"参数错误：{exc}", file=sys.stderr)
            return 1
    else:
        if args.days <= 0:
            print("参数错误：--days 必须为正整数", file=sys.stderr)
            return 1
        until = now()
        since = until - dt.timedelta(days=args.days)

    if since > until:
        print("参数错误：起始时间晚于结束时间", file=sys.stderr)
        return 1

    settings = get_settings()
    configure_logging(level=settings.LOG_LEVEL, json_format=True)
    try:
        with session_scope(settings) as db:
            rows = fetch_cost_rows(db, since=since, until=until)
    except Exception as exc:  # 入口层兜底：异常必须打印并以非零码退出
        logger.error("cost_report_failed", error=f"{type(exc).__name__}: {exc}")
        print(f"成本报表取数失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    report = build_cost_report(rows, since=since, until=until)

    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2, ensure_ascii=False))
    else:
        print(render_cost_report(report))

    logger.info(
        "cost_report_generated",
        calls=report.total.calls,
        cost_usd=str(report.total.cost_usd),
        unpriced_calls=report.total.unpriced_calls,
        has_unpriced=report.has_unpriced,
    )
    # 有未定价调用时**不算失败**——它是数据事实，不是脚本故障。
    # 但退出码保持 0：报表要能正常产出，运维自行看告警。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
