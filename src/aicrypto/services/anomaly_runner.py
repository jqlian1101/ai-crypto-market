"""异动扫描单次执行入口（PRD 23 / 22.4）。

流程：拿锁 → 从 Redis 读取去重/冷却状态 → 逐标的扫描并落库 → 落库成功后写 Redis → 更新指标。

约束：

- 仅支持 ``local`` 锁（Redis ``SET NX``）。``postgres_advisory`` 尚未实现，显式报错，不静默退化；
- Redis 状态只在数据库事务提交之后写入，避免「Redis 已标记、事件未入库」导致事件永久丢失；
- 不常驻运行：由外部调度（cron / 计划任务）周期调用。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from redis.asyncio import Redis

from aicrypto.common.clock import now
from aicrypto.config.anomaly_rules import load_anomaly_rule_config
from aicrypto.config.market_symbols import resolve_market_symbols
from aicrypto.config.settings import LockMode, Settings, get_settings
from aicrypto.infrastructure import metrics
from aicrypto.infrastructure.anomaly_state import load_last_fired
from aicrypto.infrastructure.database.session import session_scope
from aicrypto.services.anomaly_analysis import enqueue_critical_analyses
from aicrypto.services.anomaly_scan import (
    SEED_RULE_IDS,
    ScanOutcome,
    ensure_rule_version,
    load_derivatives_inputs,
    load_derivatives_thresholds,
    scan_symbol,
)
from aicrypto.services.notification_dispatch import deliver_pending_anomalies

LOCK_KEY = "anomaly_scan"
LOCK_TTL_SECONDS = 120
#: 1m 依赖规则按 PRD 22.2 / 29.2.1 必须显式禁用。
#: 实际取值以 ``base.toml`` 的 ``[anomaly.rules].disabled_rules`` 为准，
#: 这里作为配置缺失时的兜底。
DEFAULT_DISABLED_RULES = [
    "PRICE_PCT_1M",
    "PRICE_PCT_1M_CRIT",
    "PRICE_GAP",
    "PRICE_FLASH_MOVE",
]


@dataclass(frozen=True, slots=True)
class RunResult:
    status: str  # "COMPLETE" | "SKIPPED_LOCKED"
    outcomes: list[ScanOutcome] = field(default_factory=list)
    duration_ms: float = 0.0


async def _run_once_async(symbols: list[str], settings: Settings) -> RunResult:
    # 每次运行独立创建并关闭 Redis 客户端：cache.py 的单例连接绑定在首次使用的事件循环上，
    # asyncio.run() 结束后循环关闭，复用单例会在下一次调用时报 "Event loop is closed"。
    redis = Redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
    try:
        return await _run_with_client(symbols, settings, redis)
    finally:
        await redis.aclose()


async def _run_with_client(symbols: list[str], settings: Settings, redis: Redis) -> RunResult:
    started = time.perf_counter()
    cutoff = now()

    if not await _try_lock(redis, LOCK_KEY, LOCK_TTL_SECONDS):
        return RunResult(status="SKIPPED_LOCKED")

    try:
        outcomes: list[ScanOutcome] = []
        rule_config = load_anomaly_rule_config()
        # 禁用规则以 base.toml 为准（22.2 / 29.2.1）；配置缺失时用兜底常量。
        disabled_rules = (
            list(rule_config.disabled_rules)
            if rule_config.disabled_rules
            else list(DEFAULT_DISABLED_RULES)
        )
        # 阈值来自 base.toml（22.2）：配置化必须真的生效，不能只用硬编码默认值。
        derivatives_thresholds = load_derivatives_thresholds()
        for symbol in symbols:
            # 冷却状态预取：决定本轮是否产生新事件（影响落库量），必须在扫描前读取。
            cooldown_keys = [f"{symbol}:{rule_id}" for rule_id in SEED_RULE_IDS]
            last_fired = await load_last_fired(redis, cooldown_keys)

            # 去重集合在扫描时为空：指纹需由扫描结果生成，无法预取。
            # 同一 (fingerprint, revision) 的落库由数据库唯一约束兜底；
            # 「只投递一次」由投递层在发送前检查 Redis 去重键保证。
            delivered: set[str] = set()

            with session_scope(settings) as db:
                ensure_rule_version(db, now=cutoff)
                derivatives = load_derivatives_inputs(
                    db,
                    symbol=symbol,
                    cutoff=cutoff,
                    oi_window_seconds=rule_config.derivatives.oi_window_seconds,
                    funding_stale_seconds=rule_config.derivatives.funding_stale_seconds,
                )
                outcome = scan_symbol(
                    db,
                    symbol=symbol,
                    cutoff=cutoff,
                    disabled_rules=disabled_rules,
                    delivered_keys=delivered,
                    last_fired_at=last_fired,
                    derivatives=derivatives,
                    derivatives_thresholds=derivatives_thresholds,
                    cooldown_critical_seconds=settings.NOTIFY_COOLDOWN_CRITICAL_SECONDS,
                    cooldown_warn_seconds=settings.NOTIFY_COOLDOWN_WARN_SECONDS,
                )
                # PRD 22.6：CRITICAL 新事件与其完整分析入队同事务提交。
                enqueue_critical_analyses(
                    db, symbol=symbol, events=outcome.new_events, now=cutoff
                )
            # 扫描侧只落库，不写去重/冷却 Redis 状态。
            # 去重与冷却必须在「投递成功」后写入（PRD 22.4.4.1），否则投递失败会吞掉告警。
            for event in outcome.new_events:
                metrics.anomaly_events_total.labels(
                    rule_id=event.rule_id,
                    severity=event.severity,
                ).inc()
            outcomes.append(outcome)

        # 投递在锁内执行：防止两次运行并发投递同一批 PENDING 事件。
        with session_scope(settings) as db:
            await deliver_pending_anomalies(db, redis, settings=settings)

        elapsed_ms = (time.perf_counter() - started) * 1000
        metrics.anomaly_scan_duration_ms.observe(elapsed_ms)
        metrics.anomaly_scan_last_success_timestamp.set(time.time())
        return RunResult(status="COMPLETE", outcomes=outcomes, duration_ms=elapsed_ms)
    finally:
        await redis.delete(f"lock:{LOCK_KEY}")


async def _try_lock(redis: Redis, key: str, ttl_seconds: int) -> bool:
    """与 cache.acquire_lock 同键格式（``lock:{key}``），但使用调用方提供的客户端。"""
    return bool(await redis.set(f"lock:{key}", "1", nx=True, ex=ttl_seconds))


def run_anomaly_scan_once(
    symbols: list[str] | None = None, settings: Settings | None = None
) -> RunResult:
    """同步入口，供脚本或外部调度调用。

    ``symbols`` 缺省时按 32.1 优先级从环境变量 / base.toml 解析（见 ``market_symbols``）。
    """
    cfg = settings or get_settings()
    if cfg.SCHEDULER_LOCK_MODE != LockMode.LOCAL:
        raise NotImplementedError("SCHEDULER_LOCK_MODE=postgres_advisory 尚未实现，请设为 local")
    resolved = symbols if symbols is not None else resolve_market_symbols(cfg)
    return asyncio.run(_run_once_async(resolved, cfg))


__all__ = ["DEFAULT_DISABLED_RULES", "RunResult", "run_anomaly_scan_once"]
