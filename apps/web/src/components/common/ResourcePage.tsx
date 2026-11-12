"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ApiRequestError } from "@/api/client";
import type { DataQuality } from "@/api/types";
import { StateBoundary, type ViewState } from "@/components/common/StateBoundary";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { deriveViewState } from "@/lib/view-state";

export interface ResourcePageProps<T> {
  /** 查询结果。 */
  query: UseQueryResult<T>;
  /** 内容渲染。 */
  children: (data: T) => ReactNode;
  /** 数据质量。 */
  quality?: DataQuality;
  /** 是否为空。 */
  isEmpty?: (data: T) => boolean;
  /** EMPTY 状态：原因说明。 */
  emptyReason?: string;
  /** EMPTY 状态：下一步动作说明。 */
  emptyNextStep?: string;
  emptyActionHref?: string;
  emptyActionLabel?: string;
  /** 最后更新时间。 */
  lastUpdated?: string | null;
  /** TTL（秒）。 */
  ttlSeconds?: number;
  className?: string;
}

/**
 * 资源页容器：统一处理六态渲染。
 *
 * 所有数据页面都应通过本组件渲染，保证**六种状态全部被显式实现**。
 */
export function ResourcePage<T>(props: ResourcePageProps<T>) {
  const {
    query,
    children,
    quality = "GOOD",
    isEmpty,
    emptyReason = "暂无数据",
    emptyNextStep,
    emptyActionHref,
    emptyActionLabel,
    lastUpdated = null,
    ttlSeconds,
    className,
  } = props;

  const data = query.data;
  const empty = data !== undefined && isEmpty ? isEmpty(data) : false;

  const state: ViewState = deriveViewState(query, {
    quality,
    isEmpty: empty,
    lastUpdated,
    ttlSeconds,
  });

  const error = query.error;
  const errorProps =
    error instanceof ApiRequestError
      ? { code: error.code, message: error.message, requestId: error.requestId }
      : { message: error instanceof Error ? error.message : "加载失败" };

  return (
    <StateBoundary
      state={state}
      className={className}
      empty={
        <EmptyState
          reason={emptyReason}
          nextStep={emptyNextStep}
          actionHref={emptyActionHref}
          actionLabel={emptyActionLabel}
        />
      }
      error={<ErrorState {...errorProps} onRetry={() => void query.refetch()} />}
    >
      {data !== undefined ? children(data) : null}
    </StateBoundary>
  );
}