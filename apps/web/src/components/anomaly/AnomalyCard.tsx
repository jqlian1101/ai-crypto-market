/**
 * 异动卡片（PRD 第 35.6 节）。
 *
 * 显示：规则 ID、触发阈值、实测值、级别、投递状态与各 revision。
 *
 * 注意：实测值缺失时**不得**渲染为 0 或上次值（35.5 禁止项）。
 */

import type { Severity } from "@/api/types";

export interface AnomalyRevision {
  revision: number;
  /** 该 revision 的投递状态。 */
  deliveryStatus: "PENDING" | "SENT" | "FAILED" | "SKIPPED" | string;
  deliveredAt?: string | null;
  errorMessage?: string | null;
}

export interface AnomalyCardProps {
  anomalyId: string;
  /** 规则 ID。 */
  ruleId: string;
  symbol: string;
  severity: Severity;
  /** 触发阈值。 */
  threshold: string;
  /** 实测值。缺失为 null。 */
  measuredValue: number | string | null;
  /** 事件时间。 */
  occurredAt: string;
  revisions?: AnomalyRevision[];
  /** 详情页链接。 */
  href?: string;
  className?: string;
}

const SEVERITY_STYLE: Record<Severity, string> = {
  INFO: "bg-sky-50 text-sky-800 border-sky-300",
  WARN: "bg-amber-50 text-amber-800 border-amber-300",
  CRITICAL: "bg-red-50 text-red-800 border-red-300",
};

const SEVERITY_LABEL: Record<Severity, string> = {
  INFO: "提示",
  WARN: "警告",
  CRITICAL: "严重",
};

export function AnomalyCard(props: AnomalyCardProps) {
  const {
    anomalyId,
    ruleId,
    symbol,
    severity,
    threshold,
    measuredValue,
    occurredAt,
    revisions = [],
    href,
    className,
  } = props;

  return (
    <article
      className={`rounded border border-slate-200 bg-white p-4 shadow-sm ${className ?? ""}`}
      data-testid="anomaly-card"
      data-anomaly-id={anomalyId}
    >
      <header className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded border px-2 py-0.5 text-xs font-medium ${SEVERITY_STYLE[severity]}`}
          data-testid="anomaly-severity"
        >
          {SEVERITY_LABEL[severity]}
        </span>
        <span className="font-mono text-sm font-semibold" data-testid="anomaly-symbol">
          {symbol}
        </span>
        <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-xs text-slate-600" data-testid="anomaly-rule-id">
          {ruleId}
        </span>
        <time className="ml-auto text-xs text-slate-500">{occurredAt}</time>
      </header>

      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-xs text-slate-500">触发阈值</dt>
          <dd className="font-mono" data-testid="anomaly-threshold">
            {threshold}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">实测值</dt>
          <dd className="font-mono" data-testid="anomaly-measured">
            {measuredValue === null ? (
              <span className="text-slate-400">数据缺失</span>
            ) : (
              String(measuredValue)
            )}
          </dd>
        </div>
      </dl>

      {revisions.length > 0 ? (
        <div className="mt-3" data-testid="anomaly-revisions">
          <p className="mb-1 text-xs text-slate-500">投递记录</p>
          <ul className="space-y-1">
            {revisions.map((revision) => (
              <li key={revision.revision} className="flex items-center gap-2 text-xs">
                <span className="font-mono text-slate-500">rev {revision.revision}</span>
                <span data-testid="anomaly-revision-status">{revision.deliveryStatus}</span>
                {revision.deliveredAt ? (
                  <span className="text-slate-400">{revision.deliveredAt}</span>
                ) : null}
                {revision.errorMessage ? (
                  <span className="text-red-600" data-testid="anomaly-revision-error">
                    {revision.errorMessage}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {href ? (
        <a href={href} className="mt-3 inline-block text-xs text-slate-700 underline" data-testid="anomaly-detail-link">
          查看详情
        </a>
      ) : null}
    </article>
  );
}