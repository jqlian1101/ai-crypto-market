/**
 * 分析进度（PRD 第 35.2 / 35.3 节）。
 *
 * 分析进行中 → 轮询 3 秒；完成即停（PRD 35.3 表格第 3 行）。
 */

export interface AnalysisProgressProps {
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  /** 当前阶段说明。 */
  stage?: string | null;
  /** 进度 0–1。 */
  progress?: number | null;
  errorMessage?: string | null;
  className?: string;
}

const STATUS_LABELS: Record<AnalysisProgressProps["status"], string> = {
  PENDING: "排队中",
  RUNNING: "分析中",
  COMPLETED: "已完成",
  FAILED: "失败",
};

export function AnalysisProgress(props: AnalysisProgressProps) {
  const { status, stage, progress, errorMessage, className } = props;

  return (
    <div className={`rounded border border-slate-200 bg-slate-50 p-3 ${className ?? ""}`} data-testid="analysis-progress">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium" data-testid="analysis-progress-status">
          {STATUS_LABELS[status]}
        </span>
        {stage ? <span className="text-xs text-slate-500">{stage}</span> : null}
      </div>

      {status === "RUNNING" || status === "PENDING" ? (
        <div
          className="mt-2 h-1.5 w-full overflow-hidden rounded bg-slate-200"
          role="progressbar"
          aria-valuenow={progress === null || progress === undefined ? undefined : Math.round(progress * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="h-full rounded bg-slate-700 transition-all"
            style={{ width: `${Math.round((progress ?? 0) * 100)}%` }}
          />
        </div>
      ) : null}

      {status === "FAILED" ? (
        <p className="mt-2 text-xs text-red-700" data-testid="analysis-progress-error">
          {errorMessage ?? "分析失败"}
        </p>
      ) : null}
    </div>
  );
}