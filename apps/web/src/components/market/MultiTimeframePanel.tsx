/**
 * 多周期面板（PRD 第 35.6 节）。
 *
 * 职责：多周期 K 线 + 指标。
 * 要求：周期切换**不重载整页**（内部状态切换 + 按需拉取）。
 *
 * 图表库按需加载（`dynamic import`），避免首屏体积（PRD 35.11）。
 */

"use client";

import { useState } from "react";

import type { DataQuality, Timeframe } from "@/api/types";

import { QualityBanner } from "@/components/common/QualityBanner";

import { CandlestickChart } from "./CandlestickChart";

/** 周期列表。顺序即展示顺序。 */
const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "1h", "4h"];

export interface MultiTimeframePanelProps {
  symbol: string;
  quality: DataQuality;
  /** 各周期的 K 线数据。缺省则显示占位（后端端点落地后接入）。 */
  seriesByTimeframe?: Partial<Record<Timeframe, unknown>>;
  lastUpdated?: string | null;
  className?: string;
}

export function MultiTimeframePanel(props: MultiTimeframePanelProps) {
  const { symbol, quality, seriesByTimeframe, lastUpdated, className } = props;
  const [timeframe, setTimeframe] = useState<Timeframe>("1h");

  return (
    <section className={className} data-testid="multi-timeframe-panel">
      <QualityBanner quality={quality} lastUpdated={lastUpdated} />

      <div role="tablist" aria-label="周期切换" className="mb-3 flex gap-1">
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            role="tab"
            type="button"
            aria-selected={timeframe === tf}
            onClick={() => setTimeframe(tf)}
            className={`rounded px-3 py-1 text-sm ${
              timeframe === tf ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"
            }`}
          >
            {tf}
          </button>
        ))}
      </div>

      <CandlestickChart symbol={symbol} timeframe={timeframe} series={seriesByTimeframe?.[timeframe]} />
    </section>
  );
}