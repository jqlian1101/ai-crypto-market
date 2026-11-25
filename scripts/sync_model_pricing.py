#!/usr/bin/env python
"""把 ``config/base.toml`` 的模型单价同步进 ``model_pricing`` 表。

用法::

    python scripts/sync_model_pricing.py
    python scripts/sync_model_pricing.py --pricing-version v2

**什么时候需要跑**：

- 供应商调价后（改 ``base.toml`` 的 ``[llm.model_pricing]`` 并递增版本号）；
- 首次部署（让 ``llm_cost_records`` 能算出真实费用）。

**幂等**：重复跑不会产生重复行。同版本号下单价变了会**报错**——
必须递增 ``--pricing-version``，否则历史成本记录的版本号会指向新价格。
"""

from __future__ import annotations

import argparse
import sys

from aicrypto.config.model_pricing import (
    DEFAULT_PRICING_VERSION,
    PricingConfigError,
    sync_model_pricing,
)
from aicrypto.config.settings import get_settings
from aicrypto.infrastructure.database.session import session_scope
from aicrypto.infrastructure.logging import configure_logging, get_logger

logger = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="同步模型单价到 model_pricing 表")
    parser.add_argument(
        "--pricing-version",
        default=DEFAULT_PRICING_VERSION,
        help="单价版本号；调价时必须递增",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.LOG_LEVEL, json_format=True)
    try:
        with session_scope(settings) as db:
            stats = sync_model_pricing(db, pricing_version=args.pricing_version)
    except PricingConfigError as exc:
        logger.error("model_pricing_sync_failed", error=str(exc))
        print(f"同步失败：{exc}", file=sys.stderr)
        return 1
    logger.info("model_pricing_synced", **stats)
    print(
        f"单价同步完成：新增 {stats['inserted']}，跳过 {stats['skipped']}，更新 {stats['updated']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
