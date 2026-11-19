/**
 * TanStack Query 查询与轮询 Hook（PRD 第 35.3 节）。
 *
 * 刷新策略要点：
 * 1. 页面不可见（`visibilitychange`）时**暂停轮询**，恢复可见时立即刷新一次；
 * 2. 所有轮询必须使用**条件请求**（ETag / Last-Modified），服务端返回 304 时不重传数据体；
 * 3. 连续错误达 3 次后**指数退避**（5s → 15s → 60s），并在页面显示「连接异常」提示；
 * 4. **不使用 WebSocket**（除分析进度的 SSE）。
 */

import {
  useQuery,
  useQueryClient,
  type UseQueryOptions,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { POLL_BACKOFF_MS } from "@/lib/query-policy";

import { apiGet, ApiRequestError, type ApiResult } from "./client";

/** 页面可见性 Hook。不可见时返回 false，用于暂停轮询。 */
export function usePageVisible(): boolean {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const update = () => {
      setVisible(document.visibilityState === "visible");
    };
    update();
    document.addEventListener("visibilitychange", update);
    return () => {
      document.removeEventListener("visibilitychange", update);
    };
  }, []);

  return visible;
}

export interface PollOptions {
  /** 轮询间隔（毫秒）。`false` 表示不轮询。 */
  intervalMs: number | false;
  /** 查询键。 */
  queryKey: readonly unknown[];
  /** 请求路径。 */
  path: string;
  /** 页面不可见时是否暂停轮询（默认 true）。 */
  pauseWhenHidden?: boolean;
  enabled?: boolean;
  /** 是否在 401 时触发登出。 */
  onUnauthorized?: () => void;
}

/**
 * 带条件请求与可见性暂停的轮询查询。
 *
 * 条件请求：保存上次响应的 ETag / Last-Modified，下次请求带上
 * `If-None-Match` / `If-Modified-Since`；服务端返回 304 时保留旧数据。
 */
export function usePollingQuery<T>(options: PollOptions): UseQueryResult<T> {
  const {
    intervalMs,
    queryKey,
    path,
    pauseWhenHidden = true,
    enabled = true,
    onUnauthorized,
  } = options;

  const visible = usePageVisible();
  const etagRef = useRef<string | null>(null);
  const lastModifiedRef = useRef<string | null>(null);
  const queryClient = useQueryClient();

  const queryFn = useCallback(async (): Promise<T> => {
    const result: ApiResult<T> = await apiGet<T>(path, {
      etag: etagRef.current,
      lastModified: lastModifiedRef.current,
    });

    if (result.notModified) {
      // 304：服务端未变更，直接复用缓存数据，不重传数据体
      const cached = queryClient.getQueryData<T>(queryKey);
      if (cached !== undefined) {
        return cached;
      }
      throw new ApiRequestError({
        code: "COMMON_BAD_REQUEST",
        message: "服务端返回 304 但本地无缓存数据",
        status: 304,
      });
    }

    etagRef.current = result.etag;
    lastModifiedRef.current = result.lastModified;
    return result.data;
  }, [path, queryKey, queryClient]);

  const queryOptions: UseQueryOptions<T> = {
    queryKey,
    queryFn,
    enabled: enabled && (pauseWhenHidden ? visible : true),
    refetchInterval: (query) => {
      if (!intervalMs) {
        return false;
      }
      if (pauseWhenHidden && !visible) {
        return false;
      }
      const errorCount = query.state.fetchFailureCount;
      if (errorCount >= 3) {
        const index = Math.min(errorCount - 3, 2);
        return POLL_BACKOFF_MS[index] ?? 60_000;
      }
      return intervalMs;
    },
    refetchOnWindowFocus: true,
    retry: (failureCount, error) => {
      if (error instanceof ApiRequestError && error.isUnauthorized) {
        onUnauthorized?.();
        return false;
      }
      return failureCount < 2;
    },
  };

  return useQuery<T>(queryOptions);
}

/** 一次性查询（不轮询）。 */
export function useStaticQuery<T>(
  queryKey: readonly unknown[],
  path: string,
  enabled = true,
): UseQueryResult<T> {
  return useQuery<T>({
    queryKey,
    queryFn: async () => (await apiGet<T>(path)).data,
    enabled,
    retry: (failureCount, error) => {
      if (error instanceof ApiRequestError && error.isUnauthorized) {
        return false;
      }
      return failureCount < 2;
    },
  });
}

/** 探针查询（不鉴权）。 */
export function useHealthQuery(): UseQueryResult<{ status: string; timestamp: string }> {
  return useStaticQuery<{ status: string; timestamp: string }>(["health"], "/health");
}
