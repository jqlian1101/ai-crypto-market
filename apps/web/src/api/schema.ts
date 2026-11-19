/** 稳定契约别名；底层结构仅来自生成文件，不手写后端字段。 */
import type { components } from "./generated/schema";

export type ApiMeta = components["schemas"]["ResponseMeta"];
export type ApiSuccess<T> = { data: T; meta: ApiMeta };
export type ApiErrorBody = components["schemas"]["ErrorBody"];
export type ApiError = components["schemas"]["ErrorResponse"];
export type PaginationMeta = components["schemas"]["PaginationMeta"];
export type PaginatedData<T> = {
  data: T[];
  meta: ApiMeta & { pagination: PaginationMeta };
};
export type DataQuality = components["schemas"]["DataQuality"];
export type Severity = components["schemas"]["Severity"];
export type MarketType = components["schemas"]["MarketOverview"]["market_type"];
export type Timeframe = components["schemas"]["CandleSeries"]["timeframe"];
export type MarketRegime =
  | "UPTREND"
  | "DOWNTREND"
  | "RANGE"
  | "HIGH_VOLATILITY"
  | "LOW_VOLATILITY"
  | "BREAKOUT_ATTEMPT"
  | "TREND_REVERSAL"
  | "UNCERTAIN";
export type VolatilityState = "LOW" | "MEDIUM" | "HIGH";
export type HealthPayload = components["schemas"]["HealthPayload"];
export type ReadyPayload = components["schemas"]["ReadyPayload"];
export type MarketOverview = components["schemas"]["MarketOverview"];
export type CandleSeries = components["schemas"]["CandleSeries"];
export type AnalysisReport = components["schemas"]["AnalysisReportSchema"];
