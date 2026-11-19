/**
 * 前端业务类型。
 *
 * HTTP 字段来自 OpenAPI 生成文件；此文件只提供稳定的领域别名与尚未落地的
 * PRD 业务结构，不承载路由、轮询策略或服务端阈值。
 */
export type {
  ApiError,
  ApiErrorBody,
  ApiMeta,
  ApiSuccess,
  CandleSeries,
  DataQuality,
  HealthPayload,
  MarketOverview,
  MarketRegime,
  MarketType,
  PaginatedData,
  PaginationMeta,
  ReadyPayload,
  Severity,
  Timeframe,
  VolatilityState,
} from "./schema";

/** 页面状态六态（PRD 35.4）。 */
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
  value: number | null;
  sample_size: number;
  is_insufficient: boolean;
  confidence: MetricConfidence;
  detail: Record<string, unknown>;
  metric_value_deduped?: number | null;
}

export interface EvaluationGroup {
  dimension: EvaluationDimension;
  metric_name: string;
  group_key: Record<string, string>;
  metrics: EvaluationMetric[];
}
