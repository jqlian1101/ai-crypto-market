/**
 * 样本量徽标（PRD 第 35.6 / 35.7 节）。
 *
 * `N < N_min` 显示灰色「样本不足」，**不显示任何比率数值**。
 * `INSUFFICIENT_SAMPLE` 的格显示 `—` + tooltip「样本不足（n/N_min）」，
 * **不显示百分比**。
 *
 * 硬约束 2：N_min 阈值**不得**在前端硬编码 —— 由 API 下发。
 */

export interface SampleSizeBadgeProps {
  sampleSize: number;
  /** 最小样本量阈值，来自 API。 */
  minSampleSize: number;
  /** 该指标是否为置信度不足。 */
  lowConfidence?: boolean;
  className?: string;
}

export function SampleSizeBadge(props: SampleSizeBadgeProps) {
  const { sampleSize, minSampleSize, lowConfidence = false, className } = props;
  const insufficient = sampleSize < minSampleSize;

  if (insufficient) {
    return (
      <span
        className={`inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500 ${className ?? ""}`}
        title={`样本不足（${sampleSize}/${minSampleSize}）`}
        data-testid="sample-size-insufficient"
      >
        样本不足（{sampleSize}/{minSampleSize}）
      </span>
    );
  }

  return (
    <span
      className={`inline-flex items-center rounded px-2 py-0.5 text-xs ${
        lowConfidence ? "bg-yellow-50 text-yellow-800" : "bg-slate-100 text-slate-600"
      } ${className ?? ""}`}
      title={`样本量 ${sampleSize}`}
      data-testid="sample-size"
    >
      n={sampleSize}
      {lowConfidence ? " · 低置信度" : ""}
    </span>
  );
}