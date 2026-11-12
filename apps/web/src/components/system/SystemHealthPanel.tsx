/**
 * 系统健康面板（PRD 第 35.6 节）。
 */

export interface SystemHealthPanelProps {
  status: string;
  /** 各子系统检查结果。 */
  checks?: Record<string, string>;
  /** 后端版本 / 启动时间等元信息。 */
  metadata?: Record<string, string>;
  className?: string;
}

export function SystemHealthPanel(props: SystemHealthPanelProps) {
  const { status, checks = {}, metadata = {}, className } = props;
  const healthy = status.toLowerCase() === "healthy" || status.toLowerCase() === "ok";

  return (
    <section className={`rounded border bg-white p-4 ${className ?? ""}`} data-testid="system-health-panel">
      <header className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">系统健康</h2>
        <span
          className={`rounded px-2 py-0.5 text-xs font-medium ${
            healthy ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-800"
          }`}
          data-testid="system-status"
        >
          {status}
        </span>
      </header>

      {Object.keys(checks).length > 0 ? (
        <dl className="mt-3 space-y-1 text-xs">
          {Object.entries(checks).map(([name, value]) => (
            <div key={name} className="flex justify-between border-t border-slate-100 py-1">
              <dt className="text-slate-500">{name}</dt>
              <dd className="font-mono">{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {Object.keys(metadata).length > 0 ? (
        <dl className="mt-3 space-y-1 text-xs">
          {Object.entries(metadata).map(([name, value]) => (
            <div key={name} className="flex justify-between border-t border-slate-100 py-1">
              <dt className="text-slate-500">{name}</dt>
              <dd className="font-mono">{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </section>
  );
}