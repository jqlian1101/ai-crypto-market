/**
 * 视图状态推导（PRD 第 35.4 节）。
 *
 * 把 TanStack Query 的结果映射为六态之一，
 * 保证每个页面**显式区分**所有状态，而不是只处理「有数据」。
 */

import type { UseQueryResult } from "@tanstack/react-query";

import type { DataQuality } from "@/api/types";
import type { ViewState } from "@/components/common/StateBoundary";

export interface ViewStateOptions {
  /** 数据质量。 */
  quality?: DataQuality;
  /** 数据是否为空（由业务判定，如列表长度为 0）。 */
  isEmpty?: boolean;
  /** 数据最后更新时间，用于判断是否超过 TTL。 */
  lastUpdated?: string | null;
  /** TTL（秒）。默认 180。 */
  ttlSeconds?: number;
  /** 是否正在重取（已有数据但正在后台刷新）。 */
  isFetching?: boolean;
}

/**
 * 推导视图状态。
 *
 * 优先级：ERROR > LOADING > EMPTY > DEGRADED > STALE > READY。
 *
 * 注意：`DEGRADED` / `STALE` 仍然**有内容**，只是附带提示条。
 */
export function deriveViewState<T>(
  query: Pick<UseQueryResult<T>, "isPending" | "isError" | "data" | "error">,
  options: ViewStateOptions = {},
): ViewState {
  const { quality = "GOOD", isEmpty = false, lastUpdated = null, ttlSeconds = 180 } = options;

  if (query.isError) {
    return "ERROR";
  }
  if (query.isPending) {
    return "LOADING";
  }
  if (query.data === undefined) {
    return "LOADING";
  }
  if (isEmpty) {
    return "EMPTY";
  }
  if (quality === "DEGRADED") {
    return "DEGRADED";
  }
  if (quality === "STALE" || quality === "MISSING" || quality === "INVALID") {
    return "STALE";
  }
  if (lastUpdated && isExpired(lastUpdated, ttlSeconds)) {
    return "STALE";
  }
  return "READY";
}

/** 数据是否超过 TTL。 */
export function isExpired(lastUpdated: string, ttlSeconds: number, now: Date = new Date()): boolean {
  const timestamp = Date.parse(lastUpdated);
  if (Number.isNaN(timestamp)) {
    return false;
  }
  return (now.getTime() - timestamp) / 1000 > ttlSeconds;
}