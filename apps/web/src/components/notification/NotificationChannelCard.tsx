/**
 * 渠道状态卡（PRD 第 35.6 节 / 22.5.7）。
 *
 * 显示：健康、限流余量、掩码后的凭据、**人工确认项清单**。
 */

export interface NotificationChannelCardProps {
  channel: string;
  /** 渠道健康状态。 */
  healthy: boolean;
  healthMessage?: string | null;
  /** 限流余量（如 `12/60`）。 */
  rateLimitRemaining?: number | null;
  rateLimitTotal?: number | null;
  /** 掩码后的凭据（如 `tg:123456****`）。 */
  maskedCredential?: string | null;
  /** 人工确认项清单。 */
  manualChecks?: string[];
  className?: string;
}

export function NotificationChannelCard(props: NotificationChannelCardProps) {
  const {
    channel,
    healthy,
    healthMessage,
    rateLimitRemaining,
    rateLimitTotal,
    maskedCredential,
    manualChecks = [],
    className,
  } = props;

  return (
    <article
      className={`rounded border bg-white p-4 shadow-sm ${healthy ? "border-slate-200" : "border-red-300"} ${className ?? ""}`}
      data-testid="notification-channel-card"
      data-channel={channel}
    >
      <header className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">{channel}</h3>
        <span
          className={`rounded px-2 py-0.5 text-xs font-medium ${
            healthy ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-800"
          }`}
          data-testid="channel-health"
        >
          {healthy ? "健康" : "异常"}
        </span>
      </header>

      {healthMessage ? <p className="mt-1 text-xs text-slate-500">{healthMessage}</p> : null}

      <dl className="mt-3 space-y-1 text-xs">
        <div className="flex justify-between">
          <dt className="text-slate-500">限流余量</dt>
          <dd className="font-mono" data-testid="channel-rate-limit">
            {rateLimitRemaining === null || rateLimitTotal === null
              ? "—"
              : `${rateLimitRemaining}/${rateLimitTotal}`}
          </dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-slate-500">凭据（掩码）</dt>
          <dd className="font-mono" data-testid="channel-credential">
            {maskedCredential ?? "—"}
          </dd>
        </div>
      </dl>

      {manualChecks.length > 0 ? (
        <div className="mt-3" data-testid="channel-manual-checks">
          <p className="mb-1 text-xs font-medium text-slate-600">人工确认项</p>
          <ul className="list-inside list-disc space-y-0.5 text-xs text-slate-600">
            {manualChecks.map((check, index) => (
              <li key={index}>{check}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  );
}