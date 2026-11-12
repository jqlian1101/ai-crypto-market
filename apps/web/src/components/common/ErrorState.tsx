/**
 * 错误状态（PRD 第 35.4 节）。
 *
 * 错误信息 + 重试按钮 + `request_id`（便于排障）。
 */

export interface ErrorStateProps {
  /** 后端稳定错误码。 */
  code?: string;
  message: string;
  /** 后端返回的 request_id，用于排障。 */
  requestId?: string;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState(props: ErrorStateProps) {
  const { code, message, requestId, onRetry, className } = props;

  return (
    <div
      role="alert"
      className={`rounded border border-red-300 bg-red-50 p-4 ${className ?? ""}`}
      data-testid="error-state"
    >
      <p className="text-sm font-medium text-red-800">{message}</p>
      <p className="mt-1 font-mono text-xs text-red-700">
        {code ? `${code} · ` : ""}
        {requestId ? `request_id: ${requestId}` : "request_id: —"}
      </p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded bg-red-700 px-3 py-1.5 text-xs text-white hover:bg-red-600"
        >
          重试
        </button>
      ) : null}
    </div>
  );
}