/**
 * API 数据层（PRD 第 35.10 节）。
 *
 * 硬约束：
 * - 类型**从 OpenAPI 自动生成**，不手写 → `schema.d.ts` 由 `npm run generate:api` 生成
 * - 前端**不得**直接访问数据库或 Binance，一律走 `/api/v1/**`
 * - 前端**不持有**任何服务端凭据（34.3）
 * - 禁止 `any`
 */

export * from "./schema";
export * from "./client";
export * from "./auth";
export * from "./queries";
export * from "./types";
