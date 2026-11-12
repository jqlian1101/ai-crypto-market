/**
 * 市场状态徽标（PRD 第 35.6 节）。
 *
 * **多周期不一致时并列展示多个徽标**（4.6 要求）。
 * 状态标签与颜色统一映射，**颜色来自 API 或共享常量**，不散落在组件里。
 */

import type { MarketRegime } from "@/api/types";

/** 状态 → 标签/样式的统一映射（共享常量，不在组件内散落）。 */
export const REGIME_PRESENTATION: Record<
  MarketRegime,
  { label: string; className: string; description: string }
> = {
  UPTREND: {
    label: "上升趋势",
    className: "bg-emerald-50 text-emerald-800 border-emerald-300",
    description: "多头排列，回撤浅",
  },
  DOWNTREND: {
    label: "下降趋势",
    className: "bg-red-50 text-red-800 border-red-300",
    description: "空头排列，反弹弱",
  },
  RANGE: {
    label: "震荡",
    className: "bg-slate-100 text-slate-700 border-slate-300",
    description: "无明确方向",
  },
  HIGH_VOLATILITY: {
    label: "高波动",
    className: "bg-orange-50 text-orange-800 border-orange-300",
    description: "波动率显著高于常态",
  },
  LOW_VOLATILITY: {
    label: "低波动",
    className: "bg-sky-50 text-sky-800 border-sky-300",
    description: "波动率显著低于常态",
  },
  BREAKOUT_ATTEMPT: {
    label: "突破尝试",
    className: "bg-violet-50 text-violet-800 border-violet-300",
    description: "价格接近关键区间边界",
  },
  TREND_REVERSAL: {
    label: "趋势反转",
    className: "bg-amber-50 text-amber-800 border-amber-300",
    description: "趋势结构出现反转迹象",
  },
  UNCERTAIN: {
    label: "不确定",
    className: "bg-slate-100 text-slate-600 border-slate-300",
    description: "证据不足，无法判定",
  },
};

export interface MarketRegimeBadgeProps {
  regimes: MarketRegime[];
  /** 各周期对应的标签（如 `1h`、`4h`）。 */
  timeframeLabels?: string[];
  className?: string;
}

export function MarketRegimeBadge(props: MarketRegimeBadgeProps) {
  const { regimes, timeframeLabels, className } = props;

  if (regimes.length === 0) {
    return null;
  }

  return (
    <div
      className={`flex flex-wrap items-center gap-1.5 ${className ?? ""}`}
      data-testid="market-regime-badges"
    >
      {regimes.map((regime, index) => {
        const presentation = REGIME_PRESENTATION[regime];
        const timeframe = timeframeLabels?.[index];
        return (
          <span
            key={`${regime}-${index}`}
            title={presentation.description}
            data-testid="market-regime-badge"
            data-regime={regime}
            className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-medium ${presentation.className}`}
          >
            {timeframe ? <span className="opacity-70">{timeframe}</span> : null}
            {presentation.label}
          </span>
        );
      })}
    </div>
  );
}