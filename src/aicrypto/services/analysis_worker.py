"""独立分析 worker：PostgreSQL 持久队列、租约超时与可注入模型调用。

运行：python -m aicrypto.services.analysis_worker

**每次模型调用都必须被计量**（PRD 24.2 / 30.4）：

- ``model_runs`` —— 每次 HTTP 调用一行（token / 延迟 / 重试次数 / 状态）；
- ``llm_cost_records`` —— 每次调用一行的费用估算（含当时单价快照）；
- ``prompt_versions`` —— 每次用到的 Prompt 版本登记（哈希可校验）。

token 数只有供应商知道，**事后无法补**。因此计量必须发生在
「收到响应的当下」，而不是「报告生成完之后」。
"""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy import select, update

from aicrypto.common.clock import now
from aicrypto.common.errors import AICryptoError, ErrorCode
from aicrypto.common.ids import new_snapshot_id
from aicrypto.config.prompts import PromptTemplate, load_template
from aicrypto.config.settings import PROJECT_ROOT, Settings, get_settings
from aicrypto.domain.analysis.agent_output import ModelTier
from aicrypto.domain.analysis.report import (
    AnalysisMode,
    AnalysisReportContent,
    AnalysisRunStatus,
)
from aicrypto.domain.market.indicators import (
    FEATURE_VERSION,
    atr,
    bollinger,
    ema,
    macd,
    rsi,
    volume_stats,
)
from aicrypto.domain.market.structure import (
    classify_regime,
    sr_zones,
    summarize_structure,
)
from aicrypto.infrastructure import metrics
from aicrypto.infrastructure.database.models.analysis import (
    AgentRun,
    LLMCostRecord,
    MarketSnapshotRecord,
    ModelRun,
    PromptVersion,
)
from aicrypto.infrastructure.database.models.market import MarketCandle
from aicrypto.infrastructure.database.models.report import AnalysisRunRecord
from aicrypto.infrastructure.database.session import session_scope
from aicrypto.infrastructure.logging import configure_logging, get_logger
from aicrypto.services.analysis import create_report
from aicrypto.services.derivatives_snapshot import build_derivatives_snapshot
from aicrypto.services.llm_cost import PricingEntry, estimate_cost, resolve_pricing
from aicrypto.services.llm_retry import (
    AttemptOutcome,
    RetryPolicy,
    build_input_limit_error,
    build_repair_messages,
    classify_http_status,
    exceeds_input_token_limit,
    is_retryable_error,
    should_retry,
    summarize_validation_issues,
)
from aicrypto.services.market import candle_payload
from aicrypto.services.partial_result import (
    DEGRADED_REASON_FAILED,
    DEGRADED_REASON_TIMEOUT,
    build_partial_content,
)
from aicrypto.services.tier_routing import (
    TierFallbackReason,
    resolve_tier_route,
    tier_for_trigger,
)

logger = get_logger(__name__)

#: Prompt 的 Agent 目录名（prompts/<agent>/<name>.v<n>.md）
PROMPT_AGENT_NAME = "master"
#: 系统提示模板名
PROMPT_SYSTEM_TEMPLATE = "analysis"
#: 用户提示模板名
PROMPT_USER_TEMPLATE = "analysis.user"


@contextmanager
def report_scope(settings: Settings) -> Iterator[Any]:
    """报告与运行状态的事务范围。

    与计量分开——见 ``process_one`` 里关于 L-24 的说明。
    """
    with session_scope(settings) as db:
        yield db


@contextmanager
def metering_scope(settings: Settings) -> Iterator[Any]:
    """计量的独立事务范围。

    与报告事务**分开**：失败尝试同样消耗 token、同样要计费。
    若两者同事务，模型调用失败时异常穿出事务、
    **同事务里的计量行一起被回滚销毁**——
    失败烧掉的钱从来没进过账本，成本报表**系统性低估**，
    而且「越是模型不稳定的时候低估得越多」，
    恰好是你最需要看清成本的时候。

    **每次尝试独立提交**（见 ``generate_report`` 的 ``commit_metering``）：
    即使第 3 次尝试把进程搞崩了，前 2 次的账也已经记上了。
    """
    with session_scope(settings) as db:
        yield db


def claim_run(settings: Settings) -> str | None:
    moment = now()
    with session_scope(settings) as db:
        db.execute(
            update(AnalysisRunRecord)
            .where(
                AnalysisRunRecord.status == "RUNNING",
                AnalysisRunRecord.started_at
                < moment - timedelta(seconds=settings.LLM_ANALYSIS_TOTAL_BUDGET_SECONDS),
            )
            .values(status="TIMEOUT", finished_at=moment, error={"code": "COMMON_TIMEOUT"})
        )
        run = db.scalars(
            select(AnalysisRunRecord)
            .where(
                AnalysisRunRecord.status == "QUEUED",
            )
            .order_by(AnalysisRunRecord.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if run is None:
            return None
        run.status = "RUNNING"
        run.started_at = moment
        return run.run_id


def rounded(value: float | None) -> float | None:
    """指标数值统一保留 8 位小数，None 原样返回。"""
    return None if value is None else round(value, 8)


#: 每个周期取数窗口：至少覆盖 regime 的 EMA50 + 历史 100 根 ATR 分位，并为 MACD 预热留余量
FRAME_LOOKBACK: dict[str, int] = {"5m": 300, "15m": 300, "1h": 300, "4h": 300}
#: 向模型展示的最近 K 线条数（原 50，保持 token 预算）
FRAME_DISPLAY_LIMIT = 50
#: 技术指标与结构计算所需的最小有效根数（低于此值对应字段为 INSUFFICIENT）
MIN_BARS_FOR_INDICATORS = 150


def _block_has_data(block: dict[str, Any]) -> bool:
    """判断一个特征块是否含真实数据。

    ``status == "missing_data"`` 的块不算有数据。嵌套块（如 ``derivatives``
    下的 ``open_interest``）只要有任一子块为 ``ok`` 即视为有数据。
    """
    if block.get("status") != "ok":
        nested = [
            value for key, value in block.items() if key != "status" and isinstance(value, dict)
        ]
        return any(item.get("status") == "ok" for item in nested)
    return True


def _frame_indicators(rows: list[MarketCandle]) -> dict[str, Any]:
    """对单个周期计算 K 线指标与结构；样本不足的字段返回 None。"""
    highs = [row.high for row in rows]
    lows = [row.low for row in rows]
    closes = [row.close for row in rows]
    volumes = [row.volume for row in rows]
    macd_value = macd(closes)
    boll = bollinger(closes, 20, 2.0)
    vol = volume_stats(volumes, 20)
    structure = summarize_structure(highs, lows)
    regime = classify_regime(highs, lows, closes)
    zones = sr_zones(highs, lows, closes)
    return {
        "bars_used": len(rows),
        "rsi_14": rounded(rsi(closes, 14)),
        "ema_20": rounded(ema(closes, 20)),
        "ema_50": rounded(ema(closes, 50)),
        "ema_200": rounded(ema(closes, 200)),
        "macd": None
        if macd_value is None
        else {
            "macd": rounded(macd_value.macd),
            "signal": rounded(macd_value.signal),
            "histogram": rounded(macd_value.histogram),
        },
        "atr_14": rounded(atr(highs, lows, closes, 14)),
        "bollinger_20_2": None
        if boll is None
        else {
            "upper": rounded(boll.upper),
            "middle": rounded(boll.middle),
            "lower": rounded(boll.lower),
            "bandwidth": rounded(boll.bandwidth),
            "percent_b": rounded(boll.percent_b),
        },
        "volume_20": None if vol is None else {k: rounded(v) for k, v in vol.items()},
        "market_structure": {
            "bias": str(structure.bias),
            "last_swing_high": rounded(structure.last_swing_high),
            "last_swing_low": rounded(structure.last_swing_low),
            "higher_highs": structure.higher_highs,
            "higher_lows": structure.higher_lows,
            "swing_count": structure.swing_count,
        },
        "sr_zones": [
            {
                "low": rounded(z.low),
                "high": rounded(z.high),
                "touches": z.touches,
                "kind": z.kind,
            }
            for z in zones
        ],
        "regime": {
            "state": str(regime.regime),
            "ema_fast": rounded(regime.ema_fast),
            "ema_slow": rounded(regime.ema_slow),
            "atr_ratio": rounded(regime.atr_ratio),
            "atr_percentile": rounded(regime.atr_percentile),
        },
    }


def build_snapshot(run_id: str, settings: Settings) -> dict[str, Any]:
    cutoff = now()
    with session_scope(settings) as db:
        run = db.get(AnalysisRunRecord, run_id)
        if run is None or run.status != "RUNNING":
            raise AICryptoError(ErrorCode.COMMON_CONFLICT)
        frames: dict[str, Any] = {}
        indicators: dict[str, Any] = {}
        structure_by_frame: dict[str, Any] = {}
        regime_by_frame: dict[str, Any] = {}
        for frame in ("5m", "15m", "1h", "4h"):
            candles = list(
                db.scalars(
                    select(MarketCandle)
                    .where(
                        MarketCandle.symbol == run.symbol,
                        MarketCandle.market_type == run.market_type,
                        MarketCandle.exchange == "binance",
                        MarketCandle.timeframe == frame,
                        MarketCandle.is_closed.is_(True),
                        MarketCandle.close_time <= cutoff,
                        MarketCandle.received_at <= cutoff,
                        MarketCandle.processed_at <= cutoff,
                    )
                    .order_by(MarketCandle.close_time.desc())
                    .limit(FRAME_LOOKBACK[frame])
                )
            )
            ordered = list(reversed(candles))
            frames[frame] = [candle_payload(row) for row in ordered[-FRAME_DISPLAY_LIMIT:]]
            if len(ordered) >= MIN_BARS_FOR_INDICATORS:
                computed = _frame_indicators(ordered)
                structure_by_frame[frame] = computed.pop("market_structure")
                regime_by_frame[frame] = computed.pop("regime")
                indicators[frame] = computed
            else:
                indicators[frame] = {"bars_used": len(ordered), "status": "INSUFFICIENT"}
        if not any(frames.values()):
            raise AICryptoError(ErrorCode.ANALYSIS_SNAPSHOT_MISSING)
        content = {
            "multi_timeframe_ohlcv": frames,
            "indicators": indicators,
            "market_structure": structure_by_frame,
            "market_regime": regime_by_frame,
        }
        # 衍生品与流动性：数据缺失时标记 missing_data，不得用 0 替代（29.6）
        derivatives_block = build_derivatives_snapshot(
            db,
            market_type=run.market_type,
            symbol=run.symbol,
            now=cutoff,
        )
        content.update(derivatives_block)
        missing = [
            name
            for name, block in (
                ("derivatives", derivatives_block["derivatives"]),
                ("liquidity", derivatives_block["liquidity"]),
                ("basis", derivatives_block["basis"]),
            )
            if not _block_has_data(block)
        ]
        data_quality = "GOOD" if not missing else "DEGRADED"
        snapshot = MarketSnapshotRecord(
            snapshot_id=new_snapshot_id(now=cutoff),
            symbol=run.symbol,
            market_type=run.market_type,
            as_of=cutoff,
            available_as_of=cutoff,
            created_at=cutoff,
            data_quality=data_quality,
            data_quality_detail={
                "missing": missing,
                "reason": (
                    "衍生品与盘口数据齐全"
                    if not missing
                    else "部分衍生品/盘口数据缺失或陈旧，已按 missing_data 标记"
                ),
                "indicator_min_bars": MIN_BARS_FOR_INDICATORS,
            },
            feature_version=FEATURE_VERSION,
            schema_version="2",
            content=content,
            source_watermarks={"exchange": "binance", "received_before": cutoff.isoformat()},
        )
        db.add(snapshot)
        db.flush()
        run.market_snapshot_id = snapshot.snapshot_id
        return {
            "snapshot_id": snapshot.snapshot_id,
            "symbol": run.symbol,
            "as_of": cutoff.isoformat(),
            "available_as_of": cutoff.isoformat(),
            **content,
        }


def parse_report_content(raw: str) -> AnalysisReportContent:
    """兼容供应商单对象数组，内容仍执行完整领域校验。"""
    value = json.loads(raw)
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        value = value[0]
    if not isinstance(value, dict):
        raise AICryptoError(
            ErrorCode.LLM_SCHEMA_INVALID,
            details={
                "stage": "root_structure",
                "root_type": type(value).__name__,
                "length": len(value) if isinstance(value, (list, str)) else None,
                "item_types": [type(item).__name__ for item in value[:5]]
                if isinstance(value, list)
                else [],
                "item_keys": [
                    list(item.keys())[:10] for item in value[:5] if isinstance(item, dict)
                ]
                if isinstance(value, list)
                else [],
            },
        )
    return AnalysisReportContent.model_validate(value)


def resolve_model_and_provider(settings: Settings) -> tuple[str, str, str | None, str | None]:
    """解析本次调用使用的 (provider, model, base_url, api_key)。

    **保留这个函数是为了兼容旧调用方**（返回 4 元组）。
    新代码请用 :func:`tier_routing.resolve_tier_route`——
    它额外返回**档位**与**降级信息**，成本报表按档位分组依赖这些。

    这里的档位固定为 ``TIER_STANDARD``（向后兼容旧行为）。
    """
    local = str(settings.LLM_PROVIDER_DEFAULT) == "local"
    if local:
        return (
            "local",
            settings.LLM_PROVIDER_LOCAL_MODEL or "",
            settings.LLM_PROVIDER_LOCAL_BASE_URL,
            settings.LLM_PROVIDER_LOCAL_API_KEY.get_secret_value()
            if settings.LLM_PROVIDER_LOCAL_API_KEY
            else None,
        )
    return (
        "cloud",
        settings.LLM_TIER_STANDARD_MODEL or "",
        settings.LLM_CLOUD_BASE_URL,
        settings.LLM_CLOUD_API_KEY.get_secret_value() if settings.LLM_CLOUD_API_KEY else None,
    )


def extract_usage(response: httpx.Response) -> tuple[int, int]:
    """从响应中提取 token 用量。

    供应商可能不给 ``usage``，或给 ``None``。此时返回 ``(0, 0)``——
    **不报错**，因为没有 usage 不代表调用失败。

    但 ``(0, 0)`` 必须与「真实的 0 token」可区分：真实调用不会
    消耗 0 token，所以 ``(0, 0)`` 一律视为「用量未知」，
    成本记录会写入 0 并在报表中体现为用量缺失。
    """
    try:
        payload = response.json()
    except ValueError:
        return 0, 0
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0, 0
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    return (
        int(prompt_tokens) if isinstance(prompt_tokens, int) and prompt_tokens >= 0 else 0,
        int(completion_tokens)
        if isinstance(completion_tokens, int) and completion_tokens >= 0
        else 0,
    )


def record_model_run(
    db: Any,
    *,
    provider: str,
    model_id: str,
    prompt_version: str,
    status: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    retry_count: int,
    error: dict[str, Any] | None = None,
    agent_name: str | None = None,
    tier: str | None = None,
    pricing: PricingEntry | None = None,
    commit: bool = False,
) -> str:
    """写入 ``model_runs`` 与 ``llm_cost_records``，返回 ``model_run_id``。

    **两个表在同一个事务里写**：成本记录引用 ``model_run_id``，
    分开写会出现「有调用记录但没费用」或反之的孤立行。

    ``commit=True`` 时**立即提交**——用于让计量独立于报告事务存活
    （见 ``_record_attempt`` 里关于 L-24 的说明）。
    提交后仍然保持「两表同事务」的原子性：
    它们要么一起进库，要么一起回滚。
    """
    model_run_id = f"mrun_{uuid.uuid4().hex}"
    created_at = now()
    db.add(
        ModelRun(
            model_run_id=model_run_id,
            agent_run_id=None,
            provider=provider,
            model_id=model_id,
            prompt_version=prompt_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=None,
            latency_ms=latency_ms,
            retry_count=retry_count,
            status=status,
            error=error,
            created_at=created_at,
        )
    )
    estimate = estimate_cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        pricing=pricing,
    )
    # 单价缺失时 estimated_cost_usd 写 0，并在 price_snapshot 里显式标注
    # 「未定价」——**不能留 NULL**（该列 NOT NULL），也不能让读者
    # 把 0 误读成「这次调用不花钱」。快照里的 "priced": false 是显式标记。
    snapshot = estimate.price_snapshot or {
        "model_id": model_id,
        "provider": provider,
        "currency": "USD",
    }
    snapshot = {**snapshot, "priced": estimate.is_priced}
    db.add(
        LLMCostRecord(
            id=f"lcost_{uuid.uuid4().hex}",
            created_at=created_at,
            model_run_id=model_run_id,
            model_id=model_id,
            agent_name=agent_name,
            tier=tier,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimate.cost_usd if estimate.cost_usd is not None else Decimal(0),
            price_snapshot=snapshot,
            is_degraded=False,
        )
    )
    _publish_metrics(
        model_id=model_id,
        tier=tier,
        agent_name=agent_name,
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        cost_usd=estimate.cost_usd,
    )
    if commit:
        # **立即提交**：计量必须独立于报告事务存活（L-24）。
        # 报告写失败时整个事务回滚，同事务里的计量行会一起消失——
        # 失败尝试烧掉的 token 与费用从来没进过账本，
        # 成本报表因此**系统性低估**。
        db.commit()
    return model_run_id


def _publish_metrics(
    *,
    model_id: str,
    tier: str | None,
    agent_name: str | None,
    status: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    cost_usd: Decimal | None,
) -> None:
    """把一次调用同步到 Prometheus 指标。

    **这些指标此前定义了却无人写入**（与 L-16 同型的「声明未消费」）：
    ``llm_requests_total`` / ``llm_latency_ms`` / ``llm_tokens_total`` /
    ``llm_cost_usd_total`` / ``llm_daily_cost_usd`` /
    ``llm_schema_invalid_total`` 全都只有定义、没有调用方。

    指标与数据库是**两条独立的观测通道**：
    数据库是审计用的权威记录（可回溯、可对账），指标是运维用的实时信号
    （可告警、可画图）。缺了指标，线上费用突增只能等月底对账才发现。

    指标写失败**绝不能让分析任务失败**——观测通道不能影响业务通道。
    """
    agent_label = agent_name or "unknown"
    tier_label = tier or "unknown"
    try:
        metrics.llm_requests_total.labels(
            model_id=model_id,
            tier=tier_label,
            agent=agent_label,
            status=status,
        ).inc()
        metrics.llm_latency_ms.labels(model_id=model_id, tier=tier_label).observe(latency_ms)
        if input_tokens:
            metrics.llm_tokens_total.labels(model_id=model_id, direction="in").inc(input_tokens)
        if output_tokens:
            metrics.llm_tokens_total.labels(model_id=model_id, direction="out").inc(output_tokens)
        if cost_usd is not None:
            metrics.llm_cost_usd_total.labels(model_id=model_id, tier=tier_label).inc(
                float(cost_usd)
            )
            metrics.llm_daily_cost_usd.set(float(cost_usd))
        if status == "SCHEMA_INVALID":
            metrics.llm_schema_invalid_total.labels(agent=agent_label).inc()
    except Exception:  # 观测通道不得影响业务通道
        logger.warning("llm_metrics_publish_failed", model_id=model_id, status=status)


def _relative_template_path(path: Path) -> str:
    """把模板路径转成**相对仓库根**的 POSIX 风格路径。

    存相对路径而不是绝对路径：绝对路径会把开发机目录结构写进数据库，
    换机器/换容器后路径就失效了。

    路径不在仓库根之下时（理论上不会发生）退化为绝对路径字符串，
    不抛错——版本登记不应该因为路径问题而让整个分析失败。
    """
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def register_prompt_version(
    db: Any,
    *,
    template: PromptTemplate,
    change_note: str,
) -> None:
    """登记 Prompt 版本（幂等且并发安全）。

    ``prompt_versions`` 的主键是
    ``(agent_name, template_name, prompt_version)`` **三元组**——
    不是二元组。原因是**同一个 Agent 下每个模板各有独立版本号**：
    ``analysis.v1.md``（system）与 ``analysis.user.v1.md``（user）
    **都是 ``(master, v1)``**。二元组主键会让 user 模板的登记
    被**静默丢弃**（与 system 撞主键），Prompt 版本审计因此不完整。

    这里必须用**原子的 upsert**（``ON CONFLICT DO NOTHING``），而不是
    「先查再插」：后者是典型的 TOCTOU 竞态——多个 worker 并发处理
    任务时，大家都会查到「不存在」，然后一起插入，第二个开始就撞
    唯一约束，整个事务回滚，分析任务被判为 LLM_SCHEMA_INVALID。

    「先查再插」在单线程下永远是对的，所以单测抓不到这个问题；
    只有并发场景才暴露。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(PromptVersion)
        .values(
            agent_name=template.agent_name,
            template_name=template.template_name,
            prompt_version=template.version,
            template_path=_relative_template_path(template.path),
            template_hash=template.template_hash,
            description=None,
            created_at=now(),
            change_note=change_note,
        )
        .on_conflict_do_nothing(index_elements=["agent_name", "template_name", "prompt_version"])
    )
    db.execute(stmt)


def build_messages(
    *,
    system_template: PromptTemplate,
    user_template: PromptTemplate,
    snapshot: dict[str, Any],
    schema: dict[str, Any],
) -> list[dict[str, str]]:
    """按模板渲染出 messages。

    渲染失败（缺占位符）直接抛错——绝不让 ``{schema}`` 这种字面量
    发给模型。
    """
    return [
        {
            "role": "system",
            "content": system_template.render(schema=json.dumps(schema, ensure_ascii=False)),
        },
        {
            "role": "user",
            "content": user_template.render(
                snapshot=json.dumps(snapshot, ensure_ascii=False),
            ),
        },
    ]


def _estimate_input_tokens(payload: dict[str, Any]) -> int:
    """粗估一次请求的**输入** token 数。

    精确值要等模型回包才知道，那时钱已经花了——所以护栏必须**事前**拦。
    这里用「请求体字符数 / 4」估算：对中英混排的 JSON，这个系数是
    行业常用的保守近似（英文约 4 字符/token，中文更少，所以偏高估，
    正好符合「宁可拦早不要拦晚」的护栏语义）。

    宁可**高估**也不低估：低估会让超长请求发出去，
    在阶梯计价下把成本放大好几倍（成本优化 O11）。
    """
    body = json.dumps(payload, ensure_ascii=False)
    return len(body) // 4 + 1


def _post_once(
    *,
    client: httpx.Client,
    base: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> httpx.Response:
    """发出一次请求。

    单独抽出来是为了让「重试循环」只管循环，不管请求细节。
    """
    return client.post(
        f"{base.rstrip('/')}/chat/completions",
        headers=headers,
        json=payload,
    )


def _extract_output(response: httpx.Response) -> tuple[str | None, AttemptOutcome]:
    """从响应里取出模型输出文本，并判定结果分类。

    返回 ``(输出文本, 分类)``。输出为 ``None`` 表示拿不到文本
    （HTTP 失败或响应结构异常），此时分类已经说明了原因。
    """
    if not response.is_success:
        return None, classify_http_status(response.status_code)
    try:
        choice = response.json()["choices"][0]
    except (KeyError, IndexError, TypeError, ValueError):
        return None, AttemptOutcome.SCHEMA_INVALID
    if choice.get("finish_reason") == "length":
        # 输出被截断：重试同样的请求大概率还是被截断（同样的 max_tokens），
        # 但仍归为 SCHEMA_INVALID 之外的一类，让调用方明确知道原因。
        return None, AttemptOutcome.TRUNCATED
    try:
        content = choice["message"]["content"]
    except (KeyError, TypeError):
        return None, AttemptOutcome.SCHEMA_INVALID
    if not isinstance(content, str):
        return None, AttemptOutcome.SCHEMA_INVALID
    return content, AttemptOutcome.SUCCESS


def _record_attempt(
    db: Any,
    *,
    provider: str,
    model: str,
    prompt_version: str,
    status: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    retry_count: int,
    error: dict[str, Any] | None,
    tier: str,
    commit: bool = False,
) -> None:
    """记录一次 HTTP 尝试。

    **每一次实际发出的请求都写一行**——包括失败与被重试掉的那些。
    失败的尝试同样消耗 token，同样要计费；漏记会让成本报表系统性低估。

    ``tier`` 传的是**实际生效**的档位（降级后可能低于请求的档位），
    成本报表按档位分组靠的就是这一列。

    ``commit=True`` 时**立即提交**——用于让计量独立于报告事务存活
    （见 ``metering_scope`` 里关于 L-24 的说明）。
    提交后仍然保持「``model_runs`` 与 ``llm_cost_records`` 同事务」
    的原子性：它们要么一起进库，要么一起回滚。
    """
    record_model_run(
        db,
        provider=provider,
        model_id=model,
        prompt_version=prompt_version,
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        retry_count=retry_count,
        error=error,
        agent_name=PROMPT_AGENT_NAME,
        tier=tier,
        pricing=resolve_pricing(db, model_id=model, as_of=now()),
        commit=commit,
    )


def _can_fallback_to_cloud(settings: Settings) -> bool:
    """云端是否可用（能否作为本地不可用时的回退目标）。

    云端不可用时回退没有意义——那只会把「本地连不上」换成
    「云端也连不上」，还多记一次失败调用。
    """
    if not settings.LLM_CLOUD_BASE_URL:
        return False
    return bool(settings.LLM_TIER_STANDARD_MODEL or settings.LLM_TIER_LIGHT_MODEL)


def generate_report(
    snapshot: dict[str, Any],
    settings: Settings,
    *,
    db: Any,
    deadline: dt.datetime | None = None,
    tier: ModelTier = ModelTier.TIER_STANDARD,
    prefer_local: bool = False,
    call_info: dict[str, Any] | None = None,
    commit_metering: bool = False,
) -> tuple[AnalysisReportContent, str, str]:
    """调用模型生成报告。

    返回 ``(content, model_id, prompt_version)``。

    **计量在收到响应的当下完成**：token 用量与费用立刻写入
    ``model_runs`` / ``llm_cost_records``，不在报告生成后再补。

    失败路径同样要写 ``model_runs``——失败调用也消耗 token，
    也要计入成本；漏记会让成本报表系统性低估。

    **重试与修复**（本函数负责的三件事）：

    1. ``LLM_MAX_RETRIES``——只对**可重试**错误（429 / 5xx / 传输层）
       退避重试；4xx 与 schema 失败不重试（重试只是白烧钱）。
    2. ``LLM_SCHEMA_REPAIR_ATTEMPTS``——schema 校验失败时，把
       **模型上一次的输出 + 错误摘要**回灌给模型修一次，
       **不重传原始快照上下文**（PRD v2.3 / 成本优化 O12）。
    3. ``LLM_MAX_INPUT_TOKENS``——发请求前拦截超长输入
       （输入阶梯计价，超限会让成本成倍上涨，成本优化 O11）。

    **档位**（PRD 24.1）：``tier`` 决定用哪一档的模型；
    实际生效的档位（可能已降级）会写入 ``model_runs.tier`` /
    ``llm_cost_records.tier``，成本报表据此按档位分组。
    """
    route = resolve_tier_route(settings, requested_tier=tier, prefer_local=prefer_local)
    provider = route.provider
    model = route.model_id
    base = route.base_url
    secret = route.api_key
    # **实际生效**的档位（降级后可能低于请求的档位）——成本报表按它分组。
    tier_label = str(route.tier)
    #: 降级原因（None = 未降级）。发生降级时**必须在输出中标注**，
    #: 否则读者会误以为拿到的是原档位的结论。
    degraded_reason: str | None = route.degraded_reason
    if not base or not model:
        raise AICryptoError(ErrorCode.LLM_UNAVAILABLE, message="分析模型地址或模型名未配置")
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    schema = AnalysisReportContent.model_json_schema()

    system_template = load_template(PROMPT_AGENT_NAME, PROMPT_SYSTEM_TEMPLATE)
    user_template = load_template(PROMPT_AGENT_NAME, PROMPT_USER_TEMPLATE)
    messages = build_messages(
        system_template=system_template,
        user_template=user_template,
        snapshot=snapshot,
        schema=schema,
    )

    policy = RetryPolicy(max_retries=settings.LLM_MAX_RETRIES)
    repair_attempts = settings.LLM_SCHEMA_REPAIR_ATTEMPTS
    input_limit = settings.LLM_MAX_INPUT_TOKENS

    # ---- 第一次调用用的请求体 ----
    payload: dict[str, Any] = {
        "model": model,
        **(
            {"enable_thinking": settings.LLM_ENABLE_THINKING}
            if settings.LLM_ENABLE_THINKING is not None
            else {}
        ),
        "max_tokens": settings.LLM_MAX_OUTPUT_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "analysis_report",
                "schema": schema,
            },
        },
        "messages": messages,
    }

    # 输入 token 护栏：用「估算的请求体字符数 / 4」做粗估。
    # 精确 token 数要等模型回包才知道，那时钱已经花了。
    # 估算偏保守（真实 token 数通常 <= 字符数/4 的量级）。
    estimated_input = _estimate_input_tokens(payload)
    if exceeds_input_token_limit(input_tokens=estimated_input, limit=input_limit):
        raise build_input_limit_error(actual=estimated_input, limit=input_limit)

    timeout = min(settings.LLM_TIMEOUT_SECONDS, settings.LLM_ANALYSIS_TOTAL_BUDGET_SECONDS)

    # ---- 重试循环 ----
    attempt = 0
    repair_used = 0
    last_error: AICryptoError | None = None
    #: 是否已经尝试过「本地不可用 -> 回退云端」。
    #: 只回退一次：回退后仍失败就说明云端也不可用，再回退也没用。
    local_fallback_used = False

    while True:
        attempt += 1
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=timeout, trust_env=settings.LLM_TRUST_ENV) as client:
                response = _post_once(
                    client=client,
                    base=base,
                    headers=headers,
                    payload=payload,
                )
        except httpx.RequestError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            retryable = is_retryable_error(exc)
            _record_attempt(
                db,
                provider=provider,
                model=model,
                prompt_version=system_template.version,
                status="FAILED",
                input_tokens=0,
                output_tokens=0,
                latency_ms=latency_ms,
                retry_count=attempt - 1,
                error={
                    "stage": "transport",
                    "error_type": type(exc).__name__,
                    "retryable": retryable,
                },
                tier=tier_label,
                commit=commit_metering,
            )
            # **本地模型不可用 -> 回退云端**（PRD 24.3）。
            # 回退后**必须在输出中标注 degraded**——静默降级最危险：
            # 读者会以为拿到的是本地模型的结论，实际是云端模型跑的。
            if (
                retryable
                and provider == "local"
                and not local_fallback_used
                and _can_fallback_to_cloud(settings)
            ):
                local_fallback_used = True
                cloud = resolve_tier_route(
                    settings,
                    requested_tier=route.requested_tier,
                    prefer_local=False,
                )
                provider = cloud.provider
                model = cloud.model_id
                base = cloud.base_url
                secret = cloud.api_key
                tier_label = str(cloud.tier)
                headers = {"Authorization": f"Bearer {secret}"} if secret else {}
                payload = {**payload, "model": model}
                degraded_reason = str(TierFallbackReason.LOCAL_UNAVAILABLE)
                logger.warning(
                    "llm_local_fallback_to_cloud",
                    model_id=model,
                    tier=tier_label,
                    error_type=type(exc).__name__,
                )
                continue
            if retryable and should_retry(
                outcome=AttemptOutcome.RETRYABLE,
                attempt=attempt,
                policy=policy,
                deadline=deadline,
            ):
                time.sleep(policy.backoff_seconds(attempt))
                continue
            code = (
                ErrorCode.LLM_TIMEOUT
                if isinstance(exc, httpx.TimeoutException)
                else ErrorCode.LLM_UNAVAILABLE
            )
            raise AICryptoError(
                code,
                details={"stage": "transport", "error_type": type(exc).__name__},
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        input_tokens, output_tokens = extract_usage(response)
        output, outcome = _extract_output(response)

        if outcome is AttemptOutcome.SUCCESS:
            assert output is not None  # 类型收窄，非运行时断言
            try:
                content = parse_report_content(output)
            except ValidationError as exc:
                issues = [
                    {"loc": list(item["loc"]), "type": item["type"]}
                    for item in exc.errors(include_input=False, include_context=False)[:12]
                ]
                _record_attempt(
                    db,
                    provider=provider,
                    model=model,
                    prompt_version=system_template.version,
                    status="SCHEMA_INVALID",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    retry_count=attempt - 1,
                    error={"stage": "output_validation", "issues": issues},
                    tier=tier_label,
                    commit=commit_metering,
                )
                last_error = AICryptoError(
                    ErrorCode.LLM_SCHEMA_INVALID,
                    details={"stage": "output_validation", "issues": issues},
                )
                # 走「schema 修复」分支
                if repair_used < repair_attempts:
                    repair_used += 1
                    payload = {
                        **payload,
                        "messages": build_repair_messages(
                            system_template_content=system_template.content,
                            original_output=output,
                            error_summary=summarize_validation_issues(issues),
                        ),
                    }
                    continue
                raise last_error from exc
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                _record_attempt(
                    db,
                    provider=provider,
                    model=model,
                    prompt_version=system_template.version,
                    status="SCHEMA_INVALID",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    retry_count=attempt - 1,
                    error={"stage": "response_envelope"},
                    tier=tier_label,
                    commit=commit_metering,
                )
                last_error = AICryptoError(
                    ErrorCode.LLM_SCHEMA_INVALID, details={"stage": "response_envelope"}
                )
                if repair_used < repair_attempts:
                    repair_used += 1
                    payload = {
                        **payload,
                        "messages": build_repair_messages(
                            system_template_content=system_template.content,
                            original_output=output,
                            error_summary="输出不是合法的 JSON 对象",
                        ),
                    }
                    continue
                raise last_error from exc

            # 成功
            _record_attempt(
                db,
                provider=provider,
                model=model,
                prompt_version=system_template.version,
                status="SUCCESS",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                retry_count=attempt - 1,
                error=None,
                tier=tier_label,
                commit=commit_metering,
            )
            register_prompt_version(db, template=system_template, change_note="初始版本")
            register_prompt_version(db, template=user_template, change_note="初始版本")
            if call_info is not None:
                # 把实际生效的档位与降级信息回传，供调用方写入报告。
                call_info.update(
                    {
                        "tier": tier_label,
                        "requested_tier": str(route.requested_tier),
                        "provider": provider,
                        "model_id": model,
                        "degraded": degraded_reason is not None,
                        "degraded_reason": degraded_reason,
                    }
                )
            return content, model, system_template.version

        # ---- 非成功：按分类处理 ----
        if outcome is AttemptOutcome.TRUNCATED:
            _record_attempt(
                db,
                provider=provider,
                model=model,
                prompt_version=system_template.version,
                status="SCHEMA_INVALID",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                retry_count=attempt - 1,
                error={"stage": "output_truncated"},
                tier=tier_label,
                commit=commit_metering,
            )
            raise AICryptoError(ErrorCode.LLM_SCHEMA_INVALID, details={"stage": "output_truncated"})

        if outcome is AttemptOutcome.RETRYABLE:
            status = "RATE_LIMITED" if response.status_code == 429 else "FAILED"
            _record_attempt(
                db,
                provider=provider,
                model=model,
                prompt_version=system_template.version,
                status=status,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                retry_count=attempt - 1,
                error={"stage": "provider_http", "http_status": response.status_code},
                tier=tier_label,
                commit=commit_metering,
            )
            if should_retry(
                outcome=outcome,
                attempt=attempt,
                policy=policy,
                deadline=deadline,
            ):
                time.sleep(policy.backoff_seconds(attempt))
                continue
            code = (
                ErrorCode.LLM_RATE_LIMITED
                if response.status_code == 429
                else ErrorCode.LLM_UNAVAILABLE
            )
            raise AICryptoError(
                code,
                details={"stage": "provider_http", "http_status": response.status_code},
            )

        # FATAL：4xx，重试无意义
        _record_attempt(
            db,
            provider=provider,
            model=model,
            prompt_version=system_template.version,
            status="FAILED",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            retry_count=attempt - 1,
            error={"stage": "provider_http", "http_status": response.status_code},
            tier=tier_label,
            commit=commit_metering,
        )
        if response.status_code == 400:
            raise AICryptoError(
                ErrorCode.LLM_CONTENT_REJECTED,
                details={"stage": "provider_http", "http_status": response.status_code},
            )
        raise AICryptoError(
            ErrorCode.LLM_UNAVAILABLE,
            details={"stage": "provider_http", "http_status": response.status_code},
        )


def record_missing_agent(
    db: Any,
    *,
    run_id: str,
    analysis_id: str,
    symbol: str,
    market_type: str,
    snapshot_id: str | None,
    as_of: dt.datetime,
    reason: str,
    error: dict[str, Any] | None = None,
    tier: str = "TIER_STANDARD",
) -> str:
    """登记「该 Agent 的输出缺失」，**绝不用伪造输出填补**（PRD 32.4）。

    修复也失败时（``LLM_SCHEMA_REPAIR_ATTEMPTS`` 用尽仍不合 Schema），
    正确的做法是**如实记录这个 Agent 缺失**，让下游知道：
    这份结论缺少某个 Agent 的输入，而不是某个 Agent 给出了某个结论。

    **为什么不能伪造**：一个编出来的 Agent 输出会被当成真实结论参与
    汇总、进入报告、进入预测追踪。后面所有环节都无法再区分它是编的——
    这比「缺一个 Agent」危险得多。

    写入 ``agent_runs``（status=FAILED, degraded=True），
    **不写** ``agent_outputs``——没有输出就没有输出行，
    不能塞一个占位的空对象假装有输出。
    """
    agent_run_id = f"arun_{uuid.uuid4().hex}"
    started = as_of
    finished = now()
    db.add(
        AgentRun(
            run_id=agent_run_id,
            analysis_id=analysis_id,
            agent_name=PROMPT_AGENT_NAME,
            agent_version="v1",
            model_id="",
            prompt_version="",
            market_snapshot_id=snapshot_id or "",
            as_of=as_of,
            time_horizon="4h",
            status="FAILED",
            tier=tier,
            degraded=True,
            degraded_reason=reason,
            started_at=started,
            finished_at=finished,
            duration_ms=int((finished - started).total_seconds() * 1000),
            input_ref={"run_id": run_id, "symbol": symbol, "market_type": market_type},
            error=error,
        )
    )
    return agent_run_id


def process_one(settings: Settings) -> bool:
    run_id = claim_run(settings)
    if run_id is None:
        return False
    started = time.monotonic()
    # 整个任务的绝对截止时间。重试的退避等待必须落在它之内，
    # 否则一次卡住的重试会把总预算吃光，任务整体超时，
    # 还不如早点失败、把「已完成的部分」交出去。
    deadline = now() + dt.timedelta(seconds=settings.LLM_ANALYSIS_TOTAL_BUDGET_SECONDS)
    snapshot: dict[str, Any] | None = None
    # 部分结果需要的上下文。**在 try 外声明**：超时/失败的处理
    # 在 except 分支里，而快照可能已经在 try 里建好了。
    tier = ModelTier.TIER_STANDARD
    call_info: dict[str, Any] = {}
    try:
        snapshot = build_snapshot(run_id, settings)
        # **报告与计量分两个事务**（L-24）。
        #
        # 报告、运行状态走 ``report_scope``；
        # **计量走独立的 ``metering_scope``**，各自独立提交。
        #
        # 为什么必须分开：失败尝试同样消耗 token、同样要计费。
        # 若两者同事务，模型调用失败时异常穿出事务、
        # **同事务里的计量行一起被回滚销毁**——
        # 失败烧掉的钱从来没进过账本，成本报表**系统性低估**，
        # 而且「越是模型不稳定的时候低估得越多」，
        # 恰好是你最需要看清成本的时候。
        #
        # 代价是「有调用记录但没报告」的状态可能出现——
        # 但那**恰恰是真实发生过的事**（调了、钱花了、报告没写成），
        # 比悄悄抹掉更诚实。
        with report_scope(settings) as db:
            # **档位由触发来源决定**（PRD 24.1 / 24.3）：
            # 复盘 / 日报 / 分歧复盘走 DEEP，其余走 STANDARD。
            run_row = db.get(AnalysisRunRecord, run_id)
            trigger = run_row.trigger if run_row is not None else "MANUAL"
            analysis_mode = run_row.analysis_mode if run_row is not None else "LIVE"
            tier = tier_for_trigger(
                trigger,
                retrospective=analysis_mode == AnalysisMode.RETROSPECTIVE,
            )
            with metering_scope(settings) as meter_db:
                content, model_id, prompt_version = generate_report(
                    snapshot,
                    settings,
                    db=meter_db,
                    deadline=deadline,
                    tier=tier,
                    call_info=call_info,
                    commit_metering=True,
                )
            if time.monotonic() - started > settings.LLM_ANALYSIS_TOTAL_BUDGET_SECONDS:
                raise TimeoutError("分析任务超过总时限")
            create_report(
                db,
                run_id=run_id,
                market_snapshot_id=snapshot["snapshot_id"],
                content=content,
                status=AnalysisRunStatus.PARTIAL,
                now=now(),
                versions={
                    "model_id": model_id,
                    "prompt_version": prompt_version,
                    "feature_version": FEATURE_VERSION,
                    "tier": str(call_info.get("tier", tier)),
                    "requested_tier": str(call_info.get("requested_tier", tier)),
                },
                degraded_reason=call_info.get("degraded_reason"),
            )
    except Exception as exc:
        # 每项任务单独落终态；不记录模型密钥、请求头或原始响应。
        code = exc.code if isinstance(exc, AICryptoError) else ErrorCode.LLM_SCHEMA_INVALID
        status = "FAILED"
        if isinstance(exc, httpx.TimeoutException):
            code, status = ErrorCode.LLM_TIMEOUT, "TIMEOUT"
        if isinstance(exc, TimeoutError):
            code, status = ErrorCode.LLM_TIMEOUT, "TIMEOUT"
        # **超时可能被包成了 AICryptoError**（``generate_report`` 把
        # ``httpx.ReadTimeout`` 翻译成 ``AICryptoError(LLM_TIMEOUT)``）。
        # 只看异常类型会漏掉它——那会把超时报告标成「模型调用失败」，
        # **降级原因与真实原因不符**，运维按错误码排查会找错方向。
        if isinstance(exc, AICryptoError) and exc.code == ErrorCode.LLM_TIMEOUT:
            code, status = ErrorCode.LLM_TIMEOUT, "TIMEOUT"
        if isinstance(exc, AICryptoError) and exc.code == ErrorCode.LLM_SCHEMA_INVALID:
            # **修复也失败**：该 Agent 的输出视为缺失，绝不伪造（PRD 32.4）。
            # 报告不会生成——宁可没有结论，也不要一个编出来的结论。
            status = "FAILED"

        # ---- 部分结果（PRD 24.4）----
        # 「全流程超时预算用尽时，输出『分析超时 + 已完成部分』，
        # 而不是整体失败」。
        #
        # **关键前提**：快照必须已经建好。快照里是**程序算出来的
        # 确定性事实**（指标、结构、regime、支撑阻力位）——
        # 那些是真正「已完成的部分」。没有快照就没什么可交的，
        # 只能整体失败。
        #
        # **Schema 修复失败是例外**：那时模型其实**跑完了**，
        # 只是输出不合法。它的「已完成部分」不是快照里的确定性事实，
        # 而是模型给出（但不合规）的输出——那份输出无法通过校验，
        # 拿它拼一份部分报告等于把不可信内容洗成「部分可信」。
        # 所以这种情况仍然 FAILED，并且如实登记「Agent 缺失」。
        published_partial = False
        if (
            snapshot is not None
            and status in {"TIMEOUT", "FAILED"}
            and not (isinstance(exc, AICryptoError) and exc.code == ErrorCode.LLM_SCHEMA_INVALID)
        ):
            published_partial = _publish_partial_report(
                settings,
                run_id=run_id,
                snapshot=snapshot,
                error_code=str(code),
                timeout=status == "TIMEOUT",
                exception=exc,
                tier=tier,
                call_info=call_info,
            )
            if published_partial:
                status = "PARTIAL"

        with session_scope(settings) as db:
            if not published_partial:
                db.execute(
                    update(AnalysisRunRecord)
                    .where(
                        AnalysisRunRecord.run_id == run_id,
                        AnalysisRunRecord.status == "RUNNING",
                    )
                    .values(
                        status=status,
                        finished_at=now(),
                        error={
                            "code": str(code),
                            "exception_type": type(exc).__name__,
                            "details": exc.details if isinstance(exc, AICryptoError) else {},
                        },
                    )
                )
            # **修复也失败**（Schema 仍不合法）：如实登记「Agent 缺失」，
            # 绝不伪造一份输出顶上去（PRD 32.4）。
            if (
                isinstance(exc, AICryptoError)
                and exc.code == ErrorCode.LLM_SCHEMA_INVALID
                and snapshot is not None
            ):
                run_row = db.get(AnalysisRunRecord, run_id)
                if run_row is not None:
                    record_missing_agent(
                        db,
                        run_id=run_id,
                        analysis_id=run_row.analysis_id,
                        symbol=run_row.symbol,
                        market_type=run_row.market_type,
                        snapshot_id=snapshot.get("snapshot_id"),
                        as_of=now(),
                        reason=(
                            "模型输出经修复后仍不符合 Schema，"
                            f"已重试 {settings.LLM_SCHEMA_REPAIR_ATTEMPTS} 次修复"
                        ),
                        error={
                            "code": str(code),
                            "details": exc.details,
                        },
                    )
        logger.warning(
            "analysis_failed",
            run_id=run_id,
            code=str(code),
            partial_result=published_partial,
            exception_type=type(exc).__name__,
            details=exc.details if isinstance(exc, AICryptoError) else {},
        )
    return True


def _publish_partial_report(
    settings: Settings,
    *,
    run_id: str,
    snapshot: dict[str, Any],
    error_code: str,
    timeout: bool,
    exception: BaseException,
    tier: ModelTier,
    call_info: dict[str, Any],
) -> bool:
    """发布「已完成部分」的部分报告（PRD 24.4）。

    返回 ``True`` 表示发布成功（调用方应把运行状态置为 ``PARTIAL``），
    ``False`` 表示发布失败（调用方按原样落终态）。

    **失败不能被吞掉**：部分报告发布失败（比如证据校验不通过）时
    返回 ``False``，让调用方照常落 ``FAILED``/``TIMEOUT`` 终态。
    **绝不能因为「想给个部分结果」而把任务变成「成功但没结果」**——
    那比明确失败更糟：调用方以为有报告，实际没有。
    """
    snapshot_id = snapshot.get("snapshot_id")
    if not snapshot_id:
        return False
    try:
        content = build_partial_content(
            snapshot,
            snapshot_id=str(snapshot_id),
            failure_reason=_describe_failure(exception, timeout=timeout),
            error_code=error_code,
            timeout=timeout,
            time_horizon="4h",
            as_of=now(),
        )
        with session_scope(settings) as db:
            create_report(
                db,
                run_id=run_id,
                market_snapshot_id=str(snapshot_id),
                content=content,
                status=AnalysisRunStatus.PARTIAL,
                now=now(),
                versions={
                    "model_id": str(call_info.get("model_id", "")),
                    "prompt_version": "",
                    "feature_version": FEATURE_VERSION,
                    "tier": str(call_info.get("tier", tier)),
                    "requested_tier": str(call_info.get("requested_tier", tier)),
                },
                degraded_reason=(DEGRADED_REASON_TIMEOUT if timeout else DEGRADED_REASON_FAILED),
            )
    except Exception as publish_exc:
        logger.warning(
            "partial_result_publish_failed",
            run_id=run_id,
            error_code=error_code,
            exception_type=type(publish_exc).__name__,
        )
        return False
    logger.info(
        "partial_result_published",
        run_id=run_id,
        error_code=error_code,
        timeout=timeout,
    )
    return True


def _describe_failure(exception: BaseException, *, timeout: bool) -> str:
    """把异常翻译成人类可读的失败原因（写进部分报告的 summary）。"""
    if isinstance(exception, AICryptoError):
        details = exception.details or {}
        stage = details.get("stage")
        base = f"{exception.code}"
        if stage:
            return f"{base}（失败阶段：{stage}）"
        return base
    if timeout:
        return f"{type(exception).__name__}: {exception}"
    return f"{type(exception).__name__}: {exception}"


def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.LOG_LEVEL, json_format=True)
    while True:
        if not process_one(settings):
            time.sleep(1)


if __name__ == "__main__":
    main()
