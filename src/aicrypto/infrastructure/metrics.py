"""Prometheus 指标（PRD 第 37.2 章指标清单）。

**指标必须从第一天就埋**（16 章契约层落地时点），否则 Phase 1 结束后无法回溯。

本模块是指标名的唯一真源；:func:`all_metric_names` 返回全部注册指标名，
供 CI 校验与文档同步使用。
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

#: 默认注册表
REGISTRY = CollectorRegistry(auto_describe=True)

# --------------------------------------------------------------------------
# 采集层
# --------------------------------------------------------------------------
market_data_latency_ms = Histogram(
    "market_data_latency_ms",
    "交易所时间到入库完成的延迟",
    labelnames=("symbol", "market_type", "timeframe", "source"),
    buckets=(10, 50, 100, 250, 500, 1000, 2000, 5000),
    registry=REGISTRY,
)
market_candles_written_total = Counter(
    "market_candles_written_total",
    "写入的 K 线根数",
    labelnames=("symbol", "market_type", "timeframe", "source"),
    registry=REGISTRY,
)
market_gaps_total = Counter(
    "market_gaps_total",
    "检测到的 K 线缺口数",
    labelnames=("symbol", "market_type", "timeframe"),
    registry=REGISTRY,
)
market_data_quality_events_total = Counter(
    "market_data_quality_events_total",
    "数据质量事件数",
    labelnames=("rule_id", "severity"),
    registry=REGISTRY,
)
market_ws_connections_total = Counter(
    "market_ws_connections_total",
    "WebSocket 连接建立次数",
    labelnames=("market_type",),
    registry=REGISTRY,
)
market_ws_connected = Gauge(
    "market_ws_connected",
    "WebSocket 连接状态，1=已连接",
    labelnames=("market_type",),
    registry=REGISTRY,
)
market_ws_reconnects_total = Counter(
    "market_ws_reconnects_total",
    "WebSocket 重连次数",
    labelnames=("market_type",),
    registry=REGISTRY,
)
market_clock_offset_ms = Gauge(
    "market_clock_offset_ms",
    "本地时钟相对交易所时间的偏移",
    registry=REGISTRY,
)
market_binance_rate_limit_hits_total = Counter(
    "market_binance_rate_limit_hits_total",
    "交易所限流命中次数（429/418）",
    labelnames=("endpoint",),
    registry=REGISTRY,
)

# --------------------------------------------------------------------------
# 衍生品与盘口（P2）
# --------------------------------------------------------------------------
market_derivatives_oi_written_total = Counter(
    "market_derivatives_oi_written_total",
    "写入的持仓量采样数",
    labelnames=("symbol", "market_type"),
    registry=REGISTRY,
)
market_derivatives_funding_written_total = Counter(
    "market_derivatives_funding_written_total",
    "写入的资金费率记录数",
    labelnames=("symbol", "market_type"),
    registry=REGISTRY,
)
market_derivatives_book_written_total = Counter(
    "market_derivatives_book_written_total",
    "写入的盘口快照数（只存 top20）",
    labelnames=("symbol", "market_type"),
    registry=REGISTRY,
)
market_derivatives_trade_events_total = Counter(
    "market_derivatives_trade_events_total",
    "接收到的聚合成交事件数（含重复）",
    labelnames=("symbol", "market_type"),
    registry=REGISTRY,
)
market_derivatives_trades_total = Counter(
    "market_derivatives_trades_total",
    "实际落库的聚合成交数（已去重）",
    labelnames=("symbol", "market_type"),
    registry=REGISTRY,
)
market_derivatives_stale_seconds = Gauge(
    "market_derivatives_stale_seconds",
    "轮询型衍生品数据的陈旧秒数（29.4.3 通路 C）",
    labelnames=("symbol", "market_type", "dataset"),
    registry=REGISTRY,
)
market_derivatives_funding_current_rate = Gauge(
    "market_derivatives_funding_current_rate",
    "markPrice 流推送的当前预估资金费率（低延迟触发源，29.2）",
    labelnames=("symbol", "market_type"),
    registry=REGISTRY,
)
market_derivatives_mark_price = Gauge(
    "market_derivatives_mark_price",
    "markPrice 流推送的标记价格",
    labelnames=("symbol", "market_type"),
    registry=REGISTRY,
)

# --------------------------------------------------------------------------
# 特征与异动
# --------------------------------------------------------------------------
feature_compute_duration_ms = Histogram(
    "feature_compute_duration_ms",
    "特征计算耗时",
    labelnames=("feature_name",),
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500),
    registry=REGISTRY,
)
anomaly_events_total = Counter(
    "anomaly_events_total",
    "异动事件数",
    labelnames=("rule_id", "severity"),
    registry=REGISTRY,
)
anomaly_scan_duration_ms = Histogram(
    "anomaly_scan_duration_ms",
    "异动扫描耗时",
    buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000),
    registry=REGISTRY,
)
anomaly_scan_last_success_timestamp = Gauge(
    "anomaly_scan_last_success_timestamp",
    "最近一次成功扫描的 Unix 时间戳，用于监控扫描是否卡死",
    registry=REGISTRY,
)
anomaly_rule_trigger_rate = Gauge(
    "anomaly_rule_trigger_rate",
    "滚动 24h 规则触发率，用于调参",
    labelnames=("rule_id",),
    registry=REGISTRY,
)

# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------
llm_requests_total = Counter(
    "llm_requests_total",
    "LLM 请求数",
    labelnames=("model_id", "tier", "agent", "status"),
    registry=REGISTRY,
)
llm_latency_ms = Histogram(
    "llm_latency_ms",
    "LLM 调用耗时",
    labelnames=("model_id", "tier"),
    buckets=(500, 1000, 2500, 5000, 10000, 30000, 60000, 120000),
    registry=REGISTRY,
)
llm_tokens_total = Counter(
    "llm_tokens_total",
    "LLM token 消耗，direction=in|out",
    labelnames=("model_id", "direction"),
    registry=REGISTRY,
)
llm_cost_usd_total = Counter(
    "llm_cost_usd_total",
    "LLM 累计费用（美元）",
    labelnames=("model_id", "tier"),
    registry=REGISTRY,
)
llm_daily_cost_usd = Gauge(
    "llm_daily_cost_usd",
    "当日累计费用，对比日预算硬上限",
    registry=REGISTRY,
)
llm_schema_invalid_total = Counter(
    "llm_schema_invalid_total",
    "Schema 校验失败次数",
    labelnames=("agent",),
    registry=REGISTRY,
)
llm_degraded_total = Counter(
    "llm_degraded_total",
    "降级次数",
    labelnames=("reason",),
    registry=REGISTRY,
)

# --------------------------------------------------------------------------
# 通知
# --------------------------------------------------------------------------
notification_deliveries_total = Counter(
    "notification_deliveries_total",
    "通知投递结果数",
    labelnames=("channel", "delivery_status", "event_type"),
    registry=REGISTRY,
)
notification_delivery_latency_ms = Histogram(
    "notification_delivery_latency_ms",
    "通知投递耗时",
    labelnames=("channel",),
    buckets=(100, 250, 500, 1000, 2000, 3000, 5000, 10000),
    registry=REGISTRY,
)
notification_queue_depth = Gauge(
    "notification_queue_depth",
    "待发队列长度",
    labelnames=("channel",),
    registry=REGISTRY,
)
notification_dead_letters_total = Counter(
    "notification_dead_letters_total",
    "死信数",
    labelnames=("channel",),
    registry=REGISTRY,
)
notification_suppressed_total = Counter(
    "notification_suppressed_total",
    "被治理层拦截的通知数",
    labelnames=("reason",),
    registry=REGISTRY,
)
notification_daily_quota_used = Gauge(
    "notification_daily_quota_used",
    "当日已用账户级配额",
    registry=REGISTRY,
)
notification_channel_rate_limit_hits_total = Counter(
    "notification_channel_rate_limit_hits_total",
    "渠道限流次数",
    labelnames=("channel",),
    registry=REGISTRY,
)
notification_channel_health = Gauge(
    "notification_channel_health",
    "渠道健康状态，1=健康",
    labelnames=("channel",),
    registry=REGISTRY,
)

# --------------------------------------------------------------------------
# 调度与评估
# --------------------------------------------------------------------------
job_runs_total = Counter(
    "job_runs_total",
    "任务运行数",
    labelnames=("job_id", "status"),
    registry=REGISTRY,
)
job_duration_ms = Histogram(
    "job_duration_ms",
    "任务耗时",
    labelnames=("job_id",),
    buckets=(100, 500, 1000, 5000, 15000, 60000, 300000),
    registry=REGISTRY,
)
job_last_success_timestamp = Gauge(
    "job_last_success_timestamp",
    "任务最近一次成功完成的 Unix 时间戳",
    labelnames=("job_id",),
    registry=REGISTRY,
)
job_backfill_total = Counter(
    "job_backfill_total",
    "补跑次数",
    labelnames=("job_id",),
    registry=REGISTRY,
)
job_missed_total = Counter(
    "job_missed_total",
    "超出补跑上限被标记为 MISSED 的次数",
    labelnames=("job_id",),
    registry=REGISTRY,
)
evaluation_backlog = Gauge(
    "evaluation_backlog",
    "待评估预测数",
    registry=REGISTRY,
)

# --------------------------------------------------------------------------
# 执行（P1）
# --------------------------------------------------------------------------
paper_equity = Gauge(
    "paper_equity",
    "模拟账户权益",
    labelnames=("account_id",),
    registry=REGISTRY,
)
paper_open_positions = Gauge(
    "paper_open_positions",
    "模拟账户持仓数",
    labelnames=("account_id",),
    registry=REGISTRY,
)
paper_order_failures_total = Counter(
    "paper_order_failures_total",
    "模拟下单失败数",
    labelnames=("account_id", "reason"),
    registry=REGISTRY,
)
paper_reconciliation_diffs = Gauge(
    "paper_reconciliation_diffs",
    "未解释的对账差异数，>0 阻断新开仓",
    labelnames=("account_id",),
    registry=REGISTRY,
)
paper_freqtrade_health = Gauge(
    "paper_freqtrade_health",
    "Freqtrade 实例健康状态，1=健康",
    labelnames=("account_id",),
    registry=REGISTRY,
)

# --------------------------------------------------------------------------
# API 层（补充：请求级指标，PRD 37.1 要求「关键指标可观测」）
# --------------------------------------------------------------------------
http_requests_total = Counter(
    "http_requests_total",
    "HTTP 请求数",
    labelnames=("method", "path", "status"),
    registry=REGISTRY,
)
http_request_duration_ms = Histogram(
    "http_request_duration_ms",
    "HTTP 请求耗时",
    labelnames=("method", "path"),
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500),
    registry=REGISTRY,
)
auth_failures_total = Counter(
    "auth_failures_total",
    "鉴权失败次数",
    labelnames=("reason",),
    registry=REGISTRY,
)

#: 全部已注册指标名
_METRIC_OBJECTS = (
    market_data_latency_ms,
    market_candles_written_total,
    market_gaps_total,
    market_data_quality_events_total,
    market_ws_connections_total,
    market_ws_connected,
    market_ws_reconnects_total,
    market_clock_offset_ms,
    market_binance_rate_limit_hits_total,
    feature_compute_duration_ms,
    anomaly_events_total,
    anomaly_scan_duration_ms,
    anomaly_scan_last_success_timestamp,
    anomaly_rule_trigger_rate,
    llm_requests_total,
    llm_latency_ms,
    llm_tokens_total,
    llm_cost_usd_total,
    llm_daily_cost_usd,
    llm_schema_invalid_total,
    llm_degraded_total,
    notification_deliveries_total,
    notification_delivery_latency_ms,
    notification_queue_depth,
    notification_dead_letters_total,
    notification_suppressed_total,
    notification_daily_quota_used,
    notification_channel_rate_limit_hits_total,
    notification_channel_health,
    job_runs_total,
    job_duration_ms,
    job_last_success_timestamp,
    job_backfill_total,
    job_missed_total,
    evaluation_backlog,
    paper_equity,
    paper_open_positions,
    paper_order_failures_total,
    paper_reconciliation_diffs,
    paper_freqtrade_health,
    http_requests_total,
    http_request_duration_ms,
    auth_failures_total,
)


def all_metric_names() -> set[str]:
    """全部注册指标名（不含 ``_total`` 后缀归一）。"""
    names: set[str] = set()
    for metric in _METRIC_OBJECTS:
        name = metric._name
        names.add(name.removesuffix("_total"))
    return names


def render_latest() -> bytes:
    """渲染 Prometheus 文本格式。"""
    from prometheus_client import generate_latest

    return generate_latest(REGISTRY)
