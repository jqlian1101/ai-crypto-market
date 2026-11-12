/**
 * 领域类型与常量。
 *
 * 硬约束（35.1 硬约束 2）：**不得**在前端硬编码任何阈值、颜色语义、
 * 评估口径相关常量 —— 全部来自 API。
 *
 * 本文件只放**结构性常量**（枚举取值、路由路径、状态名），
 * 不含任何阈值（阈值从 `config/base.toml` 经 API 下发）。
 */

export type { DataQuality, MarketRegime, MarketType, Severity, Timeframe, VolatilityState } from "./schema";

/** 页面状态六态（PRD 35.4）。**不允许只实现「有数据」的情况。 */
export type ViewState = "LOADING" | "READY" | "EMPTY" | "STALE" | "DEGRADED" | "ERROR";

/** 评估置信度标注（PRD 33.6.2）。 */
export type MetricConfidence = "NORMAL" | "LOW_CONFIDENCE" | "INSUFFICIENT_SAMPLE";

/** 评估维度（PRD 30.5）。 */
export type EvaluationDimension =
  | "DIRECTION"
  | "PRICE"
  | "INTERVAL"
  | "TARGET"
  | "RISK"
  | "PROBABILITY"
  | "ECONOMIC";

/** 信号类型（四层统一，PRD 4.9）。 */
export type SignalType = "MARKET_BIAS" | "MARKET_OPPORTUNITY" | "TRADE_SETUP" | "EXECUTABLE_SIGNAL";

export type SignalDirection = "LONG" | "SHORT" | "NEUTRAL";

export interface EvaluationMetric {
  metric_name: string;
  /** 样本不足时为 null（PRD 33.6.2 硬约束 3）。 */
  value: number | null;
  sample_size: number;
  is_insufficient: boolean;
  confidence: MetricConfidence;
  detail: Record<string, unknown>;
  /** 去重后的值（PRD 33.7.2：两个都必须展示）。 */
  metric_value_deduped?: number | null;
}

export interface EvaluationGroup {
  dimension: EvaluationDimension;
  metric_name: string;
  group_key: Record<string, string>;
  metrics: EvaluationMetric[];
}

/** 轮询间隔（秒），对应 PRD 35.3 的刷新策略表。 */
export const POLL_INTERVALS = {
  realtimePrice: 5,
  indicators: 60,
  analysisInProgress: 3,
  listViews: 60,
  anomalyEvents: 30,
  notificationRecords: 30,
  jobRuns: 30,
} as const;

/** 连续错误后的指数退避（秒）—— PRD 35.3 规则 3。 */
export const POLL_BACKOFF_SECONDS = [5, 15, 60] as const;

/** 数据陈旧阈值（秒）—— 用于区分 READY 与 STALE 视图态。 */
export const STALE_AFTER_SECONDS = 180;

/** 路由路径（PRD 35.2 路由表）。 */
export const ROUTES = {
  home: "/",
  market: (symbol: string) => `/markets/${symbol}`,
  analysis: (analysisId: string) => `/analysis/${analysisId}`,
  opportunities: "/opportunities",
  predictions: "/predictions",
  prediction: (id: string) => `/predictions/${id}`,
  research: "/research",
  performance: "/performance",
  agents: "/agents",
  experiments: "/experiments",
  experiment: (id: string) => `/experiments/${id}`,
  anomalies: "/anomalies",
  anomaly: (id: string) => `/anomalies/${id}`,
  notifications: "/notifications",
  notificationPrefs: "/notifications/prefs",
  jobs: "/jobs",
  system: "/system",
  paper: "/paper",
  authVerify: "/auth/verify",
  /** 短链 → 具体页面的 302（PRD 35.2）。 */
  shortLink: (id: string) => `/s/${id}`,
} as const;
