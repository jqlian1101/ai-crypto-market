/**
 * OpenAPI 生成的类型占位。
 *
 * 真实类型由后端导出 OpenAPI 后生成：
 *
 * ```bash
 * npm run generate:api   # openapi-typescript http://127.0.0.1:8000/openapi.json -o src/api/schema.d.ts
 * ```
 *
 * 在生成之前，本文件提供**手工维护的最小契约类型**，仅覆盖后端已实现的端点
 * （/health、/ready、/metrics）。业务端点类型待后端端点落地后重新生成。
 *
 * 生成后请删除本文件，并把 `schema.d.ts` 纳入版本管理。
 */

/** 后端统一响应封装（PRD 31.2）。 */
export interface ApiMeta {
  request_id: string;
  trace_id?: string | null;
  generated_at: string;
}

export interface ApiSuccess<T> {
  data: T;
  meta: ApiMeta;
}

export interface ApiErrorBody {
  /** 稳定机器可读错误码，见 PRD 31.6。 */
  code: string;
  message: string;
  details: Record<string, unknown>;
  retryable: boolean;
  request_id: string;
}

export interface ApiError {
  error: ApiErrorBody;
}

/** 分页 meta（PRD 31.3）。 */
export interface PaginationMeta {
  limit: number;
  offset: number;
  total?: number | null;
  has_more: boolean;
  next_cursor?: string;
}

export interface PaginatedData<T> {
  data: T[];
  meta: ApiMeta & { pagination: PaginationMeta };
}

/** 数据质量五态（PRD 4.3 / 35.5）。 */
export type DataQuality = "GOOD" | "DEGRADED" | "STALE" | "MISSING" | "INVALID";

/** 市场状态八态（PRD 4.6）。 */
export type MarketRegime =
  | "UPTREND"
  | "DOWNTREND"
  | "RANGE"
  | "HIGH_VOLATILITY"
  | "LOW_VOLATILITY"
  | "BREAKOUT_ATTEMPT"
  | "TREND_REVERSAL"
  | "UNCERTAIN";

/** 波动状态三态（与 regime 正交，PRD 30.3）。 */
export type VolatilityState = "LOW" | "MEDIUM" | "HIGH";

export type Severity = "INFO" | "WARN" | "CRITICAL";

export type MarketType = "SPOT" | "USDM_FUTURES";

export type Timeframe = "1m" | "5m" | "15m" | "1h" | "4h";

export interface HealthPayload {
  status: string;
  timestamp: string;
}

export interface ReadyPayload {
  status: "ready" | "not_ready";
  checks: Record<string, string>;
}
