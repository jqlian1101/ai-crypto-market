/**
 * 页面状态边界（PRD 第 35.4 节）。
 *
 * 每个页面必须显式区分六种状态，不允许只实现「有数据」的情况。
 */

import type { ReactNode } from "react";

export type ViewState = "LOADING" | "READY" | "EMPTY" | "STALE" | "DEGRADED" | "ERROR";

export interface StateBoundaryProps {
  state: ViewState;
  loading?: ReactNode;
  children?: ReactNode;
  empty?: ReactNode;
  error?: ReactNode;
  className?: string;
}

export function StateBoundary(props: StateBoundaryProps) {
  const { state, loading, children, empty, error, className } = props;

  switch (state) {
    case "LOADING":
      return (
        <div className={className} aria-busy="true" data-testid="state-loading">
          {loading ?? <DefaultSkeleton />}
        </div>
      );

    case "ERROR":
      return (
        <div className={className} data-testid="state-error">
          {error ?? <p className="text-sm text-red-600">加载失败</p>}
        </div>
      );

    case "EMPTY":
      return (
        <div className={className} data-testid="state-empty">
          {empty ?? <p className="text-sm text-slate-500">暂无数据</p>}
        </div>
      );

    case "STALE":
    case "DEGRADED":
    case "READY":
    default:
      return (
        <div className={className} data-testid={`state-${state.toLowerCase()}`}>
          {children}
        </div>
      );
  }
}

function DefaultSkeleton() {
  return (
    <div className="space-y-2" data-testid="skeleton">
      <div className="h-4 w-1/3 animate-pulse rounded bg-slate-200" />
      <div className="h-4 w-2/3 animate-pulse rounded bg-slate-200" />
      <div className="h-4 w-1/2 animate-pulse rounded bg-slate-200" />
    </div>
  );
}