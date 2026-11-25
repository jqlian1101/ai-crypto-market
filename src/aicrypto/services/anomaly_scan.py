"""异动扫描编排：读 K 线 → 规则引擎 → 身份/去重判定 → 写 ``anomaly_events``。

约束（PRD 22.x / 29.5）：

- 只触发 ``USDM_FUTURES``；现货不直接触发异动（第 22 章触发源说明）；
- 只读取已收盘 K 线，且 ``close_time``、``received_at``、``processed_at`` 均不晚于 cutoff；
- 去重与冷却判定由调用方注入已投递集合与最近触发时间，本模块不直接访问 Redis；
- 写入依赖 ``(fingerprint, revision)`` 唯一约束做最终兜底，冲突视为已存在而非错误。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aicrypto.config.anomaly_rules import load_anomaly_rule_config
from aicrypto.domain.anomaly.dedup import decide_delivery
from aicrypto.domain.anomaly.derivatives_rules import DerivativesThresholds
from aicrypto.domain.anomaly.engine import (
    BarWindow,
    DerivativesInputs,
    EngineResult,
    run_engine,
)
from aicrypto.domain.anomaly.identity import (
    build_cooldown_key,
    build_event_id,
    build_fingerprint,
)
from aicrypto.domain.market.derivatives import (
    OpenInterestPoint,
    QuotePoint,
    compute_basis,
    oi_change_pct,
)
from aicrypto.infrastructure.database.models.anomaly import AnomalyEvent, AnomalyRule
from aicrypto.infrastructure.database.models.market import (
    MarketCandle,
    OrderbookSnapshot,
)
from aicrypto.services.derivatives_ingest import (
    load_funding_rates,
    load_latest_orderbook,
    load_open_interest,
)
from aicrypto.services.market import price_change_pct

SCAN_MARKET_TYPE = "USDM_FUTURES"
SCAN_SYMBOL_TIMEFRAMES = ("5m", "15m", "1h")
# 每个周期读取的根数：覆盖 ATR 预热（14）+ 基线（96）+ 当前根，并留余量。
BARS_PER_TIMEFRAME = 160
#: 价格变化率所用的 K 线周期。需与 OI 的 1h 窗口对齐（同窗口才可比）。
PRICE_TIMEFRAME = "1h"
#: 基差历史样本数（用于分位计算）。
BASIS_HISTORY_SAMPLES = 100
#: 多空偏向的历史回看窗口（秒）。
SKEW_LOOKBACK_SECONDS = 24 * 3600
#: 多空拥挤判定用的 OI 变化率窗口（秒）。1h 与 22.2.4 一致。
SKEW_OI_WINDOW_SECONDS = 3600
#: OI 变化率超过此值才确认多空方向（**小数**口径，与领域层
#: ``oi_change_pct`` 一致；0.01 表示 +1%）。
#: 低于此值说明持仓没有明显扩张，不足以判定「拥挤」。
SKEW_OI_CONFIRM_RATIO = 0.01


@dataclass(frozen=True, slots=True)
class NewEvent:
    """本轮新落库的事件；去重/冷却状态由调用方在落库成功后写入 Redis。"""

    fingerprint: str
    rule_id: str
    cooldown_key: str
    severity: str
    fired_at: dt.datetime


@dataclass(frozen=True, slots=True)
class ScanOutcome:
    symbol: str
    detected: int = 0
    persisted: int = 0
    suppressed: dict[str, int] = field(default_factory=dict)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    new_events: list[NewEvent] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LoadedBars:
    windows: dict[str, BarWindow]
    latest_open_time: dict[str, dt.datetime]


def load_bar_windows(
    db: Session,
    *,
    symbol: str,
    cutoff: dt.datetime,
    timeframes: tuple[str, ...] = SCAN_SYMBOL_TIMEFRAMES,
) -> LoadedBars:
    """读取各周期已收盘 K 线，按时间升序组装为 ``BarWindow``。

    cutoff 之后的数据一律不读，避免「未来函数」（29.5）。
    """
    windows: dict[str, BarWindow] = {}
    latest_open_time: dict[str, dt.datetime] = {}
    for frame in timeframes:
        rows = list(
            db.scalars(
                select(MarketCandle)
                .where(
                    MarketCandle.symbol == symbol,
                    MarketCandle.market_type == SCAN_MARKET_TYPE,
                    MarketCandle.exchange == "binance",
                    MarketCandle.timeframe == frame,
                    MarketCandle.is_closed.is_(True),
                    MarketCandle.close_time <= cutoff,
                    MarketCandle.received_at <= cutoff,
                    MarketCandle.processed_at <= cutoff,
                )
                .order_by(MarketCandle.close_time.desc())
                .limit(BARS_PER_TIMEFRAME)
            )
        )
        ordered = list(reversed(rows))
        if ordered:
            latest_open_time[frame] = ordered[-1].open_time
        windows[frame] = BarWindow(
            timeframe=frame,
            opens=[r.open for r in ordered],
            highs=[r.high for r in ordered],
            lows=[r.low for r in ordered],
            closes=[r.close for r in ordered],
            volumes=[r.volume for r in ordered],
        )
    return LoadedBars(windows=windows, latest_open_time=latest_open_time)


def _window_start(
    latest_open_time: Mapping[str, dt.datetime], rule_timeframe: str, cutoff: dt.datetime
) -> dt.datetime:
    """事件窗口起点：该周期最新已收盘根的 open_time；无数据时退回 cutoff。"""
    return latest_open_time.get(rule_timeframe, cutoff)


def load_derivatives_inputs(
    db: Session,
    *,
    symbol: str,
    cutoff: dt.datetime,
    oi_window_seconds: int = 3600,
    funding_stale_seconds: float = 120.0,
) -> DerivativesInputs:
    """读取衍生品规则所需的输入（PRD 22.2.4）。

    数据来源与优先级：

    - **OI 变化率**：``open_interest_history`` 的 1h 窗口变化（**百分数**）；
    - **当前资金费率**：优先用 WS ``markPrice`` 的预估费率（L-05 的 Redis 通道，
      低延迟），缺失时回落到 ``funding_history`` 中最新一条已结算费率；
    - **上一期资金费率**：``funding_history`` 中次新的一条，用于翻号判定；
    - **基差**：Spot-Futures 基差（需两侧盘口都有效，29.6）；
    - **价格变化率**：同窗口的已收盘 K 线变化率。

    **缺失一律返回 ``None``**（对应规则 skipped），不得用 0 代替。
    """
    oi_change_pct = _load_oi_change_pct(
        db, symbol=symbol, cutoff=cutoff, window_seconds=oi_window_seconds
    )
    funding_rate, previous_funding_rate = _load_funding_rates(db, symbol=symbol, cutoff=cutoff)
    basis_pct, basis_percentile = _load_basis(db, symbol=symbol, cutoff=cutoff)
    price_change_pct = _load_price_change_pct(
        db, symbol=symbol, cutoff=cutoff, window_seconds=oi_window_seconds
    )
    skew_percentile = _load_skew_percentile(
        db, symbol=symbol, cutoff=cutoff, funding_rate=funding_rate
    )
    return DerivativesInputs(
        oi_change_pct=oi_change_pct,
        funding_rate=funding_rate,
        previous_funding_rate=previous_funding_rate,
        basis_pct=basis_pct,
        basis_percentile=basis_percentile,
        skew_percentile=skew_percentile,
        price_change_pct=price_change_pct,
    )


def _load_oi_change_pct(
    db: Session,
    *,
    symbol: str,
    cutoff: dt.datetime,
    window_seconds: int,
) -> float | None:
    """1h OI 变化率（**百分数**）。数据不足时返回 ``None``。"""
    rows = load_open_interest(
        db,
        market_type=SCAN_MARKET_TYPE,
        symbol=symbol,
        since=cutoff - dt.timedelta(seconds=window_seconds * 2),
        as_of=cutoff,
    )
    if not rows:
        return None
    points = [OpenInterestPoint(ts=row.ts, open_interest=row.open_interest) for row in rows]
    change = oi_change_pct(points, window_seconds=window_seconds)
    if change is None:
        return None
    return change * 100.0


def _load_funding_rates(
    db: Session,
    *,
    symbol: str,
    cutoff: dt.datetime,
) -> tuple[float | None, float | None]:
    """（当前费率, 上一期费率）。

    当前费率优先取 WS 预估费率（L-05 的 Redis 通道）；该通道是**异步**的，
    而扫描是同步的，所以这里只能读已结算的最新一条——
    WS 通道供 L-07 的低延迟触发源使用。
    """
    rows = load_funding_rates(
        db,
        market_type=SCAN_MARKET_TYPE,
        symbol=symbol,
        as_of=cutoff,
        limit=2,
    )
    if not rows:
        return None, None
    current = float(rows[-1].funding_rate)
    previous = float(rows[-2].funding_rate) if len(rows) >= 2 else None
    return current, previous


def _load_basis(
    db: Session,
    *,
    symbol: str,
    cutoff: dt.datetime,
) -> tuple[float | None, float | None]:
    """（当前基差, 基差历史分位）。

    基差需 Spot 与 Futures 两侧盘口都有效（29.6）。
    分位基于历史基差样本——需要两侧都有过盘口快照，
    样本不足时返回 ``None``（不得用 0 冒充）。
    """
    spot = load_latest_orderbook(db, market_type="SPOT", symbol=symbol, as_of=cutoff)
    futures = load_latest_orderbook(db, market_type=SCAN_MARKET_TYPE, symbol=symbol, as_of=cutoff)
    if spot is None or futures is None:
        return None, None
    spot_point = QuotePoint(
        market_type="SPOT",
        captured_at=spot.captured_at,
        best_bid=spot.best_bid,
        best_ask=spot.best_ask,
    )
    futures_point = QuotePoint(
        market_type=SCAN_MARKET_TYPE,
        captured_at=futures.captured_at,
        best_bid=futures.best_bid,
        best_ask=futures.best_ask,
    )
    result = compute_basis(spot_point, futures_point)
    if not result.is_available or result.basis_pct is None:
        return None, None
    return result.basis_pct, _basis_percentile(db, symbol=symbol, cutoff=cutoff)


def _basis_percentile(
    db: Session,
    *,
    symbol: str,
    cutoff: dt.datetime,
) -> float | None:
    """当前**绝对基差**在历史绝对基差样本中的分位（0–1）。

    样本不足时返回 ``None``——不能用 0 冒充（0 意味着「处于最低分位」）。
    """
    spot_rows = list(
        db.scalars(
            select(OrderbookSnapshot)
            .where(
                OrderbookSnapshot.exchange == "binance",
                OrderbookSnapshot.market_type == "SPOT",
                OrderbookSnapshot.symbol == symbol.upper(),
                OrderbookSnapshot.captured_at <= cutoff,
            )
            .order_by(OrderbookSnapshot.captured_at.desc())
            .limit(BASIS_HISTORY_SAMPLES)
        )
    )
    futures_rows = list(
        db.scalars(
            select(OrderbookSnapshot)
            .where(
                OrderbookSnapshot.exchange == "binance",
                OrderbookSnapshot.market_type == SCAN_MARKET_TYPE,
                OrderbookSnapshot.symbol == symbol.upper(),
                OrderbookSnapshot.captured_at <= cutoff,
            )
            .order_by(OrderbookSnapshot.captured_at.desc())
            .limit(BASIS_HISTORY_SAMPLES)
        )
    )
    if len(spot_rows) < 2 or len(futures_rows) < 2:
        return None
    futures_by_time = {row.captured_at: row for row in futures_rows}
    samples: list[float] = []
    for spot_row in spot_rows:
        match = futures_by_time.get(spot_row.captured_at)
        if match is None:
            continue
        spot_point = QuotePoint(
            market_type="SPOT",
            captured_at=spot_row.captured_at,
            best_bid=spot_row.best_bid,
            best_ask=spot_row.best_ask,
        )
        futures_point = QuotePoint(
            market_type=SCAN_MARKET_TYPE,
            captured_at=match.captured_at,
            best_bid=match.best_bid,
            best_ask=match.best_ask,
        )
        computed = compute_basis(spot_point, futures_point)
        if computed.is_available and computed.basis_pct is not None:
            samples.append(abs(computed.basis_pct))
    if len(samples) < 2:
        return None
    # ``spot_rows`` 按 captured_at **降序**排列，因此 samples[0] 才是**最新**样本。
    # 「当前值」必须取最新的那个——用 samples[-1] 会拿到最旧的样本，
    # 导致分位算在历史值上而非当前值上（基差极端规则因此永远不触发）。
    current = samples[0]
    return percentile_of(samples, current)


def percentile_of(samples: list[float], value: float) -> float:
    """``value`` 在 ``samples`` 中的分位（0–1）。

    采用「小于计数 + 相等计半」的口径，与领域层 ``funding_percentile`` 一致。
    """
    if not samples:
        return 0.0
    below = sum(1 for sample in samples if sample < value)
    equal = sum(1 for sample in samples if sample == value)
    return (below + equal / 2) / len(samples)


def _load_price_change_pct(
    db: Session,
    *,
    symbol: str,
    cutoff: dt.datetime,
    window_seconds: int,
) -> float | None:
    """同窗口价格变化率（**百分数**）。"""
    return price_change_pct(
        db,
        symbol=symbol,
        market_type=SCAN_MARKET_TYPE,
        timeframe=PRICE_TIMEFRAME,
        window_seconds=window_seconds,
        as_of=cutoff,
    )


def _load_skew_percentile(
    db: Session,
    *,
    symbol: str,
    cutoff: dt.datetime,
    funding_rate: float | None,
) -> float | None:
    """多空比的历史分位（0–1）。

    多空比本身不可直接观测（Binance 的 ``globalLongShortAccountRatio``
    未被采集），这里用**与领域层 ``long_short_skew`` 一致的联合信号**构造标量：
    资金费率的**符号**表示多空方向，**绝对值**表示拥挤程度，
    持仓量变化确认方向。

    构造的标量：

    - 多头拥挤 → 正值（费率正 + OI 上升）；
    - 空头拥挤 → 负值（费率负 + OI 上升）；
    - 其余 → 0。

    取该标量的历史分位：≥P95 为多头拥挤，≤P5 为空头拥挤。

    历史样本不足时返回 ``None``（不得用 0 冒充——0 意味着「恰好中性」）。
    """
    if funding_rate is None:
        return None
    funding_rows = load_funding_rates(
        db, market_type=SCAN_MARKET_TYPE, symbol=symbol, as_of=cutoff, limit=100
    )
    oi_rows = load_open_interest(
        db,
        market_type=SCAN_MARKET_TYPE,
        symbol=symbol,
        since=cutoff - dt.timedelta(seconds=SKEW_LOOKBACK_SECONDS),
        as_of=cutoff,
    )
    if len(funding_rows) < 2 or len(oi_rows) < 2:
        return None
    oi_points = [OpenInterestPoint(ts=row.ts, open_interest=row.open_interest) for row in oi_rows]
    samples: list[float] = []
    for row in funding_rows:
        # 每个历史样本必须配**它自己那个时点**的 OI 变化率。
        # 若套用当前的 OI 变化，所有历史样本的标量会相同，
        # 分位恒为 0.5，``DER_LS_SKEW`` 永远不会触发。
        historical_oi_change = oi_change_pct(
            oi_points,
            window_seconds=SKEW_OI_WINDOW_SECONDS,
            end_ts=row.funding_time,
        )
        samples.append(_skew_scalar(float(row.funding_rate), historical_oi_change))
    if len(samples) < 2:
        return None
    current_oi_change = oi_change_pct(oi_points, window_seconds=SKEW_OI_WINDOW_SECONDS)
    return percentile_of(samples, _skew_scalar(funding_rate, current_oi_change))


def _skew_scalar(funding_rate: float, oi_change: float | None) -> float:
    """把「资金费率 + OI 变化」映射成多空拥挤标量。

    正值 = 多头拥挤，负值 = 空头拥挤，0 = 中性。
    与领域层 ``long_short_skew`` 的判定保持一致。

    **单位**：``oi_change`` 是**小数**（领域层 ``oi_change_pct`` 的返回口径，
    0.3 表示 +30%）。阈值必须同口径——用百分数比较会让
    0.3 < 1.0 恒成立，多空拥挤永远无法被确认。
    """
    if funding_rate == 0:
        return 0.0
    if oi_change is not None and oi_change > SKEW_OI_CONFIRM_RATIO:
        return funding_rate
    return 0.0


def load_derivatives_thresholds() -> DerivativesThresholds:
    """从 ``base.toml`` 加载衍生品规则阈值。

    这是「配置化」的唯一入口——规则阈值必须来自配置文件，
    不能依赖调用方是否记得传参（否则会静默回落到硬编码默认值）。
    """
    config = load_anomaly_rule_config()
    der = config.derivatives
    return DerivativesThresholds(
        oi_change_warn_pct=der.oi_change_warn_pct,
        oi_change_critical_pct=der.oi_change_critical_pct,
        funding_abs_warn=der.funding_abs_warn,
        funding_abs_critical=der.funding_abs_critical,
        divergence_pct=der.divergence_pct,
        basis_percentile=der.basis_percentile,
        ls_skew_percentile=der.ls_skew_percentile,
    )


def scan_symbol(
    db: Session,
    *,
    symbol: str,
    cutoff: dt.datetime,
    disabled_rules: list[str],
    delivered_keys: set[str],
    last_fired_at: dict[str, dt.datetime],
    derivatives: DerivativesInputs | None = None,
    derivatives_thresholds: DerivativesThresholds | None = None,
) -> ScanOutcome:
    """对单个合约标的执行一轮扫描。

    ``delivered_keys`` / ``last_fired_at`` 由调用方从 Redis 注入；本函数只做纯判定与写库。
    ``derivatives`` 为衍生品规则输入；``None`` 表示未加载，相关规则计入 skipped。

    ``derivatives_thresholds`` 未显式传入时，**从 ``base.toml`` 加载**，
    而不是回落到硬编码默认值——否则「改配置不生效」的问题会在
    调用方忘记传参时静默复现（配置化是假的）。
    """
    if derivatives_thresholds is None:
        derivatives_thresholds = load_derivatives_thresholds()
    ensure_rule_version(db, now=cutoff)
    loaded = load_bar_windows(db, symbol=symbol, cutoff=cutoff)
    engine: EngineResult = run_engine(
        bars=loaded.windows,
        disabled_rules=disabled_rules,
        derivatives=derivatives,
        derivatives_thresholds=derivatives_thresholds,
    )

    suppressed: dict[str, int] = {}
    new_events: list[NewEvent] = []
    persisted = 0
    errors = [f"{e.rule_id}: {e.message}" for e in engine.errors]

    for hit in engine.hits:
        base_rule = hit.rule_id.removesuffix("_CRIT")
        window_start = _window_start(
            loaded.latest_open_time, _timeframe_for_rule(base_rule), cutoff
        )
        fingerprint = build_fingerprint(
            symbol=symbol,
            market_type=SCAN_MARKET_TYPE,
            rule_id=hit.rule_id,
            window_start=window_start,
        )
        cooldown_key = build_cooldown_key(symbol=symbol, rule_id=hit.rule_id)
        decision = decide_delivery(
            fingerprint=fingerprint,
            revision=0,
            severity=hit.severity,
            cooldown_key=cooldown_key,
            now=cutoff,
            delivered_keys=delivered_keys,
            last_fired_at=last_fired_at,
        )
        if not decision.allowed:
            suppressed[decision.reason] = suppressed.get(decision.reason, 0) + 1
            continue

        values: dict[str, Any] = {
            "anomaly_event_id": build_event_id(fingerprint, 0),
            "detected_at": cutoff,
            "symbol": symbol,
            "market_type": SCAN_MARKET_TYPE,
            "rule_id": hit.rule_id,
            "rule_version": rule_version_for(hit.rule_id),
            "severity": hit.severity,
            "detected_by": "RULE_ENGINE",
            "window_start": window_start,
            "window_end": cutoff,
            "market_snapshot_id": None,
            "metrics": dict(hit.metrics),
            "threshold": dict(hit.threshold),
            "direction": hit.direction,
            "cooldown_key": cooldown_key,
            "fingerprint": fingerprint,
            "revision": 0,
            "revision_reason": "INITIAL_DETERMINISTIC",
            "notification_status": "PENDING",
        }
        # 用 RETURNING 判断是否真正新增：冲突跳过时不返回行。
        # 不依赖 rowcount，因为它在 ON CONFLICT DO NOTHING 下的语义不稳定。
        stmt = (
            insert(AnomalyEvent)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_anomaly_events_fp_rev")
            .returning(AnomalyEvent.anomaly_event_id)
        )
        inserted = db.execute(stmt).scalar_one_or_none()
        if inserted is not None:
            persisted += 1
            new_events.append(
                NewEvent(
                    fingerprint=fingerprint,
                    rule_id=hit.rule_id,
                    cooldown_key=cooldown_key,
                    severity=hit.severity,
                    fired_at=cutoff,
                )
            )

    return ScanOutcome(
        symbol=symbol,
        detected=len(engine.hits),
        persisted=persisted,
        suppressed=suppressed,
        skipped=len(engine.skipped),
        errors=errors,
        new_events=new_events,
    )


# v1 初始规则定义（PRD 22.2 初始阈值）。修改阈值须新增版本，不改已有行（30.6）。
# 引擎输出的每个 rule_id（含 _CRIT 变体）都必须有对应规则版本行，否则外键违例。
_V1_RULES: tuple[dict[str, Any], ...] = (
    {"rule_id": "PRICE_PCT_5M", "category": "PRICE", "severity": "WARN", "push_mode": "PUSH"},
    {
        "rule_id": "PRICE_PCT_5M_CRIT",
        "category": "PRICE",
        "severity": "CRITICAL",
        "push_mode": "PUSH",
    },
    {"rule_id": "PRICE_PCT_15M", "category": "PRICE", "severity": "WARN", "push_mode": "PUSH"},
    {
        "rule_id": "PRICE_PCT_15M_CRIT",
        "category": "PRICE",
        "severity": "CRITICAL",
        "push_mode": "PUSH",
    },
    {"rule_id": "PRICE_PCT_1H", "category": "PRICE", "severity": "WARN", "push_mode": "PUSH"},
    {
        "rule_id": "PRICE_PCT_1H_CRIT",
        "category": "PRICE",
        "severity": "CRITICAL",
        "push_mode": "PUSH",
    },
    {"rule_id": "VOL_ATR_SPIKE", "category": "VOLATILITY", "severity": "WARN", "push_mode": "PUSH"},
    {
        "rule_id": "VOL_ATR_SPIKE_CRIT",
        "category": "VOLATILITY",
        "severity": "CRITICAL",
        "push_mode": "PUSH",
    },
    {"rule_id": "VOL_REL_VOLUME", "category": "VOLUME", "severity": "WARN", "push_mode": "PUSH"},
    {
        "rule_id": "VOL_REL_VOLUME_CRIT",
        "category": "VOLUME",
        "severity": "CRITICAL",
        "push_mode": "PUSH",
    },
)

#: 当前生效的规则版本。
#:
#: v1 是 PRD 22.2 的价格/波动/成交量初始阈值；v2 新增**衍生品规则族**
#: （PRD 22.2.4）并补充衍生品阈值段。按 PRD 30.6「改阈值不改已有行、
#: 新增版本」的约定，v1 的行保持不动，v2 另起。
RULE_VERSION = "v2"

# v2 新增的衍生品规则定义（PRD 22.2.4）。
_V2_RULES: tuple[dict[str, Any], ...] = (
    {"rule_id": "DER_OI_SURGE", "category": "DERIVATIVES", "severity": "WARN", "push_mode": "PUSH"},
    {
        "rule_id": "DER_OI_SURGE_CRIT",
        "category": "DERIVATIVES",
        "severity": "CRITICAL",
        "push_mode": "PUSH",
    },
    {
        "rule_id": "DER_OI_FLUSH",
        "category": "DERIVATIVES",
        "severity": "CRITICAL",
        "push_mode": "PUSH",
    },
    {
        "rule_id": "DER_FUNDING_EXTREME",
        "category": "DERIVATIVES",
        "severity": "WARN",
        "push_mode": "PUSH",
    },
    {
        "rule_id": "DER_FUNDING_EXTREME_CRIT",
        "category": "DERIVATIVES",
        "severity": "CRITICAL",
        "push_mode": "PUSH",
    },
    {
        "rule_id": "DER_FUNDING_FLIP",
        "category": "DERIVATIVES",
        "severity": "INFO",
        "push_mode": "AGGREGATE",
    },
    {
        "rule_id": "DER_BASIS_EXTREME",
        "category": "DERIVATIVES",
        "severity": "WARN",
        "push_mode": "PUSH",
    },
    {
        "rule_id": "DER_LS_SKEW",
        "category": "DERIVATIVES",
        "severity": "WARN",
        "push_mode": "PUSH",
    },
    {
        "rule_id": "DER_PRICE_OI_DIVERGENCE",
        "category": "DERIVATIVES",
        "severity": "WARN",
        "push_mode": "PUSH",
    },
)

#: 全部需要写入规则版本表的 rule_id（含各版本的 _CRIT 变体）。
SEED_RULE_IDS = tuple(r["rule_id"] for r in _V1_RULES + _V2_RULES)

#: 规则版本 -> 该版本的规则定义。
_RULE_VERSIONS: tuple[tuple[str, tuple[dict[str, Any], ...], str], ...] = (
    ("v1", _V1_RULES, "初始版本"),
    ("v2", _V2_RULES, "新增衍生品规则族（PRD 22.2.4）"),
)

#: rule_id -> 该规则所属的阈值版本。
#:
#: **每条规则行绑定它自己的阈值版本**（PRD 30.6：改阈值不改已有行、
#: 新增版本）。v1 的价格/波动/成交量规则阈值未变，仍属 v1；
#: 只有新增的 ``DER_*`` 属 v2。事件落库时按此表取版本，
#: 不能一律用「当前版本」——那会让旧规则引用不存在的版本行（外键违例）。
_RULE_VERSION_BY_ID: dict[str, str] = {
    rule["rule_id"]: version for version, rules, _ in _RULE_VERSIONS for rule in rules
}


def rule_version_for(rule_id: str) -> str:
    """某条规则所属的阈值版本。未知规则回落到当前版本。"""
    return _RULE_VERSION_BY_ID.get(rule_id, RULE_VERSION)


def ensure_rule_version(db: Session, *, now: dt.datetime) -> None:
    """幂等写入规则定义，保证 ``anomaly_events`` 的复合外键可满足。

    **每个版本写各自的行**，不修改已有行（PRD 30.6：改阈值不改已有行、
    新增版本）。因此历史事件仍能引用它当时的规则版本行。
    """
    for version, rules, change_note in _RULE_VERSIONS:
        for rule in rules:
            stmt = (
                insert(AnomalyRule)
                .values(
                    rule_id=rule["rule_id"],
                    rule_version=version,
                    category=rule["category"],
                    description=f"{rule['rule_id']} 规则（{change_note}）",
                    severity=rule["severity"],
                    push_mode=rule["push_mode"],
                    params={},
                    asset_overrides={},
                    market_type=SCAN_MARKET_TYPE,
                    enabled=True,
                    effective_from=now,
                    effective_to=None,
                    created_at=now,
                    change_note=change_note,
                )
                .on_conflict_do_nothing(constraint="pk_anomaly_rules")
            )
            db.execute(stmt)


def _timeframe_for_rule(base_rule_id: str) -> str:
    if base_rule_id.startswith("PRICE_PCT_5M"):
        return "5m"
    if base_rule_id.startswith("PRICE_PCT_15M"):
        return "15m"
    if base_rule_id.startswith("PRICE_PCT_1H"):
        return "1h"
    # 波动与成交量规则基于 1h。
    return "1h"


__all__ = [
    "BARS_PER_TIMEFRAME",
    "RULE_VERSION",
    "SCAN_MARKET_TYPE",
    "SEED_RULE_IDS",
    "ScanOutcome",
    "load_bar_windows",
    "load_derivatives_inputs",
    "rule_version_for",
    "scan_symbol",
]
