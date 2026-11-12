/**
 * 空状态（PRD 第 35.4 节）。
 *
 * **EMPTY 必须给出下一步动作** —— 例如「本周期无异动记录」应附带
 * 「查看异动规则」链接；「无已到期预测」应说明「预测将在窗口结束后自动评估」。
 */

import Link from "next/link";
import type { ReactNode } from "react";

export interface EmptyStateProps {
  /** 空状态的原因说明。 */
  reason: string;
  /** 下一步动作说明。 */
  nextStep?: string;
  /** 下一步动作的链接。 */
  actionHref?: string;
  actionLabel?: string;
  children?: ReactNode;
  className?: string;
}

export function EmptyState(props: EmptyStateProps) {
  const { reason, nextStep, actionHref, actionLabel, children, className } = props;

  return (
    <div
      className={`rounded border border-dashed border-slate-300 bg-slate-50 p-6 text-center ${className ?? ""}`}
      data-testid="empty-state"
    >
      <p className="text-sm font-medium text-slate-700">{reason}</p>
      {nextStep ? <p className="mt-1 text-xs text-slate-500">{nextStep}</p> : null}
      {actionHref && actionLabel ? (
        <Link
          href={actionHref}
          className="mt-3 inline-block rounded bg-slate-800 px-3 py-1.5 text-xs text-white hover:bg-slate-700"
        >
          {actionLabel}
        </Link>
      ) : null}
      {children}
    </div>
  );
}