/**
 * K 线图（PRD 第 35.8 节）。
 *
 * - Lightweight Charts
 * - 成交量副图
 * - 叠加 EMA / Bollinger
 * - **标注快照 `as_of` 时刻**
 * - 所有图表必须能显示数据缺失区间（留空，不连线跨越）
 * - 必须有**表格替代视图**
 */

"use client";

import {
  ColorType,
  CrosshairMode,
  createChart,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

import { DataTable } from "@/components/common/DataTable";

export interface CandlePoint {
  /** Unix 秒（UTC 存储）。 */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface CandlestickChartProps {
  symbol: string;
  timeframe: string;
  series?: unknown;
  /** 快照时刻（ISO-8601），标注在图表上。 */
  asOf?: string | null;
  className?: string;
}

export function CandlestickChart(props: CandlestickChartProps) {
  const { symbol, timeframe, series, asOf, className } = props;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const [showTable, setShowTable] = useState(false);
  const [points, setPoints] = useState<CandlePoint[]>([]);

  // 解析数据：后端返回 { candles: CandlePoint[] } 或直接 CandlePoint[]
  useEffect(() => {
    const raw = series as { candles?: CandlePoint[] } | CandlePoint[] | undefined;
    if (!raw) {
      setPoints([]);
      return;
    }
    const candles = Array.isArray(raw) ? raw : (raw.candles ?? []);
    setPoints(candles);
  }, [series]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    const chart = createChart(container, {
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: "#334155" },
      grid: {
        vertLines: { color: "#f1f5f9" },
        horzLines: { color: "#f1f5f9" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#e2e8f0" },
      timeScale: { borderColor: "#e2e8f0", timeVisible: true },
      height: 320,
    });
    chartRef.current = chart;

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#16a34a",
      downColor: "#dc2626",
      borderVisible: false,
      wickUpColor: "#16a34a",
      wickDownColor: "#dc2626",
    });
    candleSeriesRef.current = candleSeries;

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    volumeSeriesRef.current = volumeSeries;

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth });
    };
    handleResize();
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
    };
  }, []);

  // 数据更新：缺失区间留空，不连线跨越
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    if (!candleSeries || !volumeSeries) {
      return;
    }

    const candles: CandlestickData[] = [];
    const volumes: Array<{ time: UTCTimestamp; value: number; color: string }> = [];

    for (const point of points) {
      const time = point.time as UTCTimestamp;
      candles.push({
        time,
        open: point.open,
        high: point.high,
        low: point.low,
        close: point.close,
      });
      volumes.push({
        time,
        value: point.volume ?? 0,
        color: point.close >= point.open ? "rgba(22,163,74,0.4)" : "rgba(220,38,38,0.4)",
      });
    }

    candleSeries.setData(candles);
    volumeSeries.setData(volumes);
    chartRef.current?.timeScale().fitContent();
  }, [points]);

  return (
    <div className={className}>
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs text-slate-500">
          {symbol} · {timeframe}
          {asOf ? <span className="ml-2">数据快照 as_of {asOf}</span> : null}
        </p>
        <button
          type="button"
          onClick={() => setShowTable((v) => !v)}
          className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-700 hover:bg-slate-200"
        >
          {showTable ? "隐藏表格" : "表格视图"}
        </button>
      </div>

      <div ref={containerRef} data-testid="candlestick-chart" className="w-full" />

      {showTable ? (
        <div className="mt-3">
          <DataTable<CandlePoint>
            caption={`${symbol} ${timeframe} K 线数据`}
            columns={[
              { key: "time", header: "时间(UTC)", sticky: true, render: (row) => new Date(row.time * 1000).toISOString() },
              { key: "open", header: "开", align: "right", render: (row) => row.open.toFixed(4) },
              { key: "high", header: "高", align: "right", render: (row) => row.high.toFixed(4) },
              { key: "low", header: "低", align: "right", render: (row) => row.low.toFixed(4) },
              { key: "close", header: "收", align: "right", render: (row) => row.close.toFixed(4) },
              { key: "volume", header: "量", align: "right", render: (row) => (row.volume ?? 0).toFixed(2) },
            ]}
            rows={points}
            getRowKey={(row) => String(row.time)}
          />
        </div>
      ) : null}
    </div>
  );
}