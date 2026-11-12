/**
 * 涨跌展示。
 *
 * 颜色语义全局统一：涨=绿 / 跌=红（PRD 35.8 通用约定 3）。
 * 颜色取自 Tailwind 共享配置（`market.up` / `market.down`），不在组件里散落。
 */

export interface PriceChangeProps {
  /** 涨跌百分比。 */
  value: number | null;
  /** 数据陈旧或缺失时隐藏。 */
  hidden?: boolean;
  className?: string;
}

export function PriceChange(props: PriceChangeProps) {
  const { value, hidden = false, className } = props;

  if (hidden || value === null) {
    return (
      <span className={`text-sm text-slate-400 ${className ?? ""}`} data-testid="price-change-hidden">
        —
      </span>
    );
  }

  const isUp = value > 0;
  const isFlat = value === 0;
  const colorClass = isFlat ? "text-slate-600" : isUp ? "text-market-up" : "text-market-down";

  return (
    <span className={`text-sm font-mono tabular-nums ${colorClass} ${className ?? ""}`} data-testid="price-change">
      {isUp ? "+" : ""}
      {value.toFixed(2)}%
    </span>
  );
}