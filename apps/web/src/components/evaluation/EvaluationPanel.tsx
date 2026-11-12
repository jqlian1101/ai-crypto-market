/**
 * 双轨评估面板（PRD 第 35.6 / 35.7 节）。
 *
 * 强制规则：
 * - **左右并排**（25.5），不可折叠其一
 * - 显示 `sample_size` 与 `INSUFFICIENT_SAMPLE` 标记
 * - `INSUFFICIENT_SAMPLE` 的格显示 `—` + tooltip，**不显示百分比**
 * - `LOW_CONFIDENCE` 显示值但带黄色标记
 * - 样本不足的组**不参与排名**，显示为 `—` 而非末位
 * - 去重前后的比率同时显示（33.7.2）
 */

import type { EvaluationGroup, MetricConfidence } from "@/api/types";

import { SampleSizeBadge } from "@/components/common/SampleSizeBadge";

export interface EvaluationPanelProps {
  /** 左轨。 */
  trackA: { title: string; groups: EvaluationGroup[] };
  /** 右轨。 */
  trackB: { title: string; groups: EvaluationGroup[] };
  /** 口径版本与生效时间（页面顶部展示）。 */
  evaluatorVersion?: string | null;
  evaluatorEffectiveFrom?: string | null;
  className?: string;
}

export function EvaluationPanel(props: EvaluationPanelProps) {
  const { trackA, trackB, evaluatorVersion, evaluatorEffectiveFrom, className } = props;

  return (
    <section className={className} data-testid="evaluation-panel">
      {evaluatorVersion ? (
        <p className="mb-3 text-xs text-slate-500" data-testid="evaluator-version">
          评估口径版本 {evaluatorVersion}
          {evaluatorEffectiveFrom ? ` · 生效于 ${evaluatorEffectiveFrom}` : ""}
        </p>
      ) : null}

      {/* 双轨并排，不可折叠其一 */}
      <div className="grid gap-4 md:grid-cols-2" data-testid="evaluation-tracks">
        <Track title={trackA.title} groups={trackA.groups} />
        <Track title={trackB.title} groups={trackB.groups} />
      </div>
    </section>
  );
}

interface TrackProps {
  title: string;
  groups: EvaluationGroup[];
}

function Track(props: TrackProps) {
  const { title, groups } = props;

  return (
    <div className="rounded border border-slate-200 bg-white p-3" data-testid="evaluation-track">
      <h3 className="mb-2 text-sm font-semibold">{title}</h3>
      {groups.length === 0 ? (
        <p className="text-xs text-slate-500">本轨暂无评估结果</p>
      ) : (
        <ul className="space-y-3">
          {groups.map((group, index) => (
            <li key={index} data-testid="evaluation-group">
              <p className="text-xs text-slate-600">{group.metric_name}</p>
              <table className="mt-1 w-full text-sm">
                <tbody>
                  {group.metrics.map((metric, metricIndex) => (
                    <tr key={metricIndex} className="border-t border-slate-100">
                      <td className="py-1 text-xs text-slate-600">{metric.metric_name}</td>
                      <td className="py-1 text-right font-mono tabular-nums">
                        <MetricValue
                          value={metric.value}
                          confidence={metric.confidence}
                          dedupedValue={metric.metric_value_deduped}
                        />
                      </td>
                      <td className="py-1 pl-2 text-right">
                        <SampleSizeBadge
                          sampleSize={metric.sample_size}
                          minSampleSize={sampleSizeMin(group)}
                          lowConfidence={metric.confidence === "LOW_CONFIDENCE"}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function sampleSizeMin(group: EvaluationGroup): number {
  const detail = group.metrics[0]?.detail;
  if (detail && typeof detail === "object") {
    const min = (detail as Record<string, unknown>)["n_min"];
    if (typeof min === "number") {
      return min;
    }
  }
  return Number.POSITIVE_INFINITY;
}

interface MetricValueProps {
  value: number | null;
  confidence: MetricConfidence;
  dedupedValue?: number | null;
}

function MetricValue(props: MetricValueProps) {
  const { value, confidence, dedupedValue } = props;

  if (confidence === "INSUFFICIENT_SAMPLE" || value === null) {
    // 不显示任何比率数值
    return (
      <span className="text-slate-400" title="样本不足" data-testid="metric-insufficient">
        —
      </span>
    );
  }

  const formatted = `${(value * 100).toFixed(1)}%`;

  return (
    <span className={confidence === "LOW_CONFIDENCE" ? "text-yellow-700" : ""} data-testid="metric-value">
      {formatted}
      {dedupedValue !== undefined && dedupedValue !== null ? (
        <span className="ml-1 text-xs text-slate-500" title="去重后">
          (去重 {(dedupedValue * 100).toFixed(1)}%)
        </span>
      ) : null}
    </span>
  );
}