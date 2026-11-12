/**
 * 置信度展示（PRD 第 35.6 节）。
 *
 * 必须附带「主观置信度，非统计胜率」提示（4.7 要求）。
 */

export interface ConfidenceMeterProps {
  /** 0–1 的置信度。 */
  value: number | null;
  /** 是否样本不足 —— 不足时显示 `—`。 */
  insufficientSample?: boolean;
  /** 最低样本量阈值（来自 API）。 */
  minSampleSize?: number;
  sampleSize?: number;
  className?: string;
}

export function ConfidenceMeter(props: ConfidenceMeterProps) {
  const { value, insufficientSample = false, className } = props;

  const percent = value === null ? null : Math.round(value * 100);

  return (
    <div className={className} data-testid="confidence-meter">
      <div className="flex items-center gap-2">
        <div
          className="h-2 w-32 overflow-hidden rounded bg-slate-200"
          role="meter"
          aria-valuenow={insufficientSample || percent === null ? undefined : percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="主观置信度"
        >
          <div
            className="h-full rounded bg-slate-500"
            style={{ width: insufficientSample || percent === null ? "0%" : `${percent}%` }}
          />
        </div>
        <span className="font-mono text-sm tabular-nums">
          {insufficientSample || percent === null ? "—" : `${percent}%`}
        </span>
      </div>
      <p className="mt-1 text-xs text-slate-500">主观置信度，非统计胜率</p>
    </div>
  );
}