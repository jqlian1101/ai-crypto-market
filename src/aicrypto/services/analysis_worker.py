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

import json
import time
import uuid
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
from aicrypto.domain.analysis.report import AnalysisReportContent, AnalysisRunStatus
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
from aicrypto.infrastructure.database.models.analysis import (
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
from aicrypto.services.market import candle_payload

logger = get_logger(__name__)

#: Prompt 的 Agent 目录名（prompts/<agent>/<name>.v<n>.md）
PROMPT_AGENT_NAME = "master"
#: 系统提示模板名
PROMPT_SYSTEM_TEMPLATE = "analysis"
#: 用户提示模板名
PROMPT_USER_TEMPLATE = "analysis.user"


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

    档位固定为 ``TIER_STANDARD``：本阶段只接通标准档（24.1 的
    ``TIER_LIGHT`` / ``TIER_DEEP`` 属于后续阶段）。但**档位要显式记录**，
    否则成本报表无法按档位分组。
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
) -> str:
    """写入 ``model_runs`` 与 ``llm_cost_records``，返回 ``model_run_id``。

    **两个表在同一个事务里写**：成本记录引用 ``model_run_id``，
    分开写会出现「有调用记录但没费用」或反之的孤立行。
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
    return model_run_id


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

    ``prompt_versions`` 的主键是 ``(agent_name, prompt_version)`` 组合，
    重复插入会抛唯一约束冲突。

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
            prompt_version=template.version,
            template_path=_relative_template_path(template.path),
            template_hash=template.template_hash,
            description=None,
            created_at=now(),
            change_note=change_note,
        )
        .on_conflict_do_nothing(index_elements=["agent_name", "prompt_version"])
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


def generate_report(
    snapshot: dict[str, Any],
    settings: Settings,
    *,
    db: Any,
) -> tuple[AnalysisReportContent, str, str]:
    """调用模型生成报告。

    返回 ``(content, model_id, prompt_version)``。

    **计量在收到响应的当下完成**：token 用量与费用立刻写入
    ``model_runs`` / ``llm_cost_records``，不在报告生成后再补。

    失败路径同样要写 ``model_runs``——失败调用也消耗 token，
    也要计入成本；漏记会让成本报表系统性低估。
    """
    provider, model, base, secret = resolve_model_and_provider(settings)
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

    started = time.perf_counter()
    try:
        with httpx.Client(
            timeout=min(settings.LLM_TIMEOUT_SECONDS, settings.LLM_ANALYSIS_TOTAL_BUDGET_SECONDS),
            trust_env=settings.LLM_TRUST_ENV,
        ) as client:
            response = client.post(
                f"{base.rstrip('/')}/chat/completions",
                headers=headers,
                json={
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
                },
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        input_tokens, output_tokens = extract_usage(response)

        if not response.is_success:
            record_model_run(
                db,
                provider=provider,
                model_id=model,
                prompt_version=system_template.version,
                status="FAILED",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                retry_count=0,
                error={"stage": "provider_http", "http_status": response.status_code},
                agent_name=PROMPT_AGENT_NAME,
                tier="TIER_STANDARD",
                pricing=resolve_pricing(db, model_id=model, as_of=now()),
            )
            raise AICryptoError(
                ErrorCode.LLM_UNAVAILABLE,
                details={"stage": "provider_http", "http_status": response.status_code},
            )
        try:
            choice = response.json()["choices"][0]
            if choice.get("finish_reason") == "length":
                record_model_run(
                    db,
                    provider=provider,
                    model_id=model,
                    prompt_version=system_template.version,
                    status="SCHEMA_INVALID",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    retry_count=0,
                    error={"stage": "output_truncated"},
                    agent_name=PROMPT_AGENT_NAME,
                    tier="TIER_STANDARD",
                    pricing=resolve_pricing(db, model_id=model, as_of=now()),
                )
                raise AICryptoError(
                    ErrorCode.LLM_SCHEMA_INVALID, details={"stage": "output_truncated"}
                )
            content = parse_report_content(choice["message"]["content"])
        except ValidationError as exc:
            record_model_run(
                db,
                provider=provider,
                model_id=model,
                prompt_version=system_template.version,
                status="SCHEMA_INVALID",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                retry_count=0,
                error={
                    "stage": "output_validation",
                    "issues": [
                        {"loc": list(item["loc"]), "type": item["type"]}
                        for item in exc.errors(include_input=False, include_context=False)[:12]
                    ],
                },
                agent_name=PROMPT_AGENT_NAME,
                tier="TIER_STANDARD",
                pricing=resolve_pricing(db, model_id=model, as_of=now()),
            )
            raise AICryptoError(
                ErrorCode.LLM_SCHEMA_INVALID,
                details={
                    "stage": "output_validation",
                    "issues": [
                        {"loc": list(item["loc"]), "type": item["type"]}
                        for item in exc.errors(include_input=False, include_context=False)[:12]
                    ],
                },
            ) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            record_model_run(
                db,
                provider=provider,
                model_id=model,
                prompt_version=system_template.version,
                status="SCHEMA_INVALID",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                retry_count=0,
                error={"stage": "response_envelope"},
                agent_name=PROMPT_AGENT_NAME,
                tier="TIER_STANDARD",
                pricing=resolve_pricing(db, model_id=model, as_of=now()),
            )
            raise AICryptoError(
                ErrorCode.LLM_SCHEMA_INVALID, details={"stage": "response_envelope"}
            ) from exc

        record_model_run(
            db,
            provider=provider,
            model_id=model,
            prompt_version=system_template.version,
            status="SUCCESS",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            retry_count=0,
            error=None,
            agent_name=PROMPT_AGENT_NAME,
            tier="TIER_STANDARD",
            pricing=resolve_pricing(db, model_id=model, as_of=now()),
        )
        register_prompt_version(db, template=system_template, change_note="初始版本")
        register_prompt_version(db, template=user_template, change_note="初始版本")
        return content, model, system_template.version
    except httpx.RequestError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        record_model_run(
            db,
            provider=provider,
            model_id=model,
            prompt_version=system_template.version,
            status="FAILED",
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            retry_count=0,
            error={"stage": "transport", "error_type": type(exc).__name__},
            agent_name=PROMPT_AGENT_NAME,
            tier="TIER_STANDARD",
            pricing=resolve_pricing(db, model_id=model, as_of=now()),
        )
        raise


def process_one(settings: Settings) -> bool:
    run_id = claim_run(settings)
    if run_id is None:
        return False
    started = time.monotonic()
    try:
        snapshot = build_snapshot(run_id, settings)
        # 计量写入与报告创建在**同一个事务**里：成本记录引用 model_run_id，
        # 报告的 versions 引用 model_id / prompt_version，三者要么一起成功、
        # 要么一起回滚，不允许出现「有报告无成本」或「有成本无报告」。
        with session_scope(settings) as db:
            content, model_id, prompt_version = generate_report(snapshot, settings, db=db)
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
                },
            )
    except Exception as exc:
        # 每项任务单独落终态；不记录模型密钥、请求头或原始响应。
        code = exc.code if isinstance(exc, AICryptoError) else ErrorCode.LLM_SCHEMA_INVALID
        status = "FAILED"
        if isinstance(exc, httpx.RequestError):
            code = ErrorCode.LLM_UNAVAILABLE
        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            code, status = ErrorCode.LLM_TIMEOUT, "TIMEOUT"
        with session_scope(settings) as db:
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
        logger.warning("analysis_failed", run_id=run_id, code=str(code))
    return True


def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.LOG_LEVEL, json_format=True)
    while True:
        if not process_one(settings):
            time.sleep(1)


if __name__ == "__main__":
    main()
