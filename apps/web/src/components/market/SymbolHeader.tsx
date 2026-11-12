/**
 * 标的头部（PRD 第 35.6 节）。
 *
 * 职责：标的、当前价、涨跌、市场状态、数据质量徽标。
 *
 * 注意：数据质量为 `STALE` 时**隐藏实时价格**（35.5）；
 * `MISSING` 时**不显示 0 或上一次的值**。
 */

import type { DataQuality, MarketRegime } from "@/api/types";

import { MarketRegimeBadge } from "./MarketRegimeBadge";
import { PriceChange } from "./PriceChange";

export interface SymbolHeaderProps {
  symbol: string;
  /** 当前价。STALE / MISSING 时应传 null。 */
  price: number | null;
  /** 24h 涨跌百分比。 */
  changePercent?: number | null;
  marketType?: "SPOT" | "USDM_FUTURES";
  regimes?: MarketRegime[];
  timeframeLabels?: string[];
  quality: DataQuality;
  /** 最后更新时间。 */
  lastUpdated?: string | null;
  className?: string;
}

export function SymbolHeader(props: SymbolHeaderProps) {
  const { symbol, price, changePercent, marketType, regimes, timeframeLabels, quality, lastUpdated, className } =
    props;

  // STALE 隐藏实时价格，MISSING 不显示任何数值
  const hidePrice = quality === "STALE" || quality === "MISSING";

  return (
    <header className={`flex flex-wrap items-start justify-between gap-4 ${className ?? ""}`}>
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight" data-testid="symbol-header-title">
            {symbol}
          </h1>
          {marketType ? (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">{marketType}</span>
          ) : null}
        </div>
        <div className="mt-1 flex items-baseline gap-3">
          {hidePrice ? (
            <span className="text-2xl font-mono text-slate-400" data-testid="symbol-price-hidden">
              {quality === "MISSING" ? "数据缺失" : "—"}
            </span>
          ) : (
            <span className="text-2xl font-mono tabular-nums" data-testid="symbol-price">
              {price === null ? "—" : price}
            </span>
          )}
          <PriceChange value={changePercent ?? null} hidden={hidePrice} />
        </div>
        {lastUpdated ? (
          <p className="mt-1 text-xs text-slate-500" data-testid="symbol-last-updated">
            最后更新：{lastUpdated}
          </p>
        ) : null}
      </div>
      {regimes ? <MarketRegimeBadge regimes={regimes} timeframeLabels={timeframeLabels} /> : null}
    </header>
  );
}