/**
 * 预测对照（PRD 第 35.8 节）。
 *
 * 在价格图上叠加预测区间带 + 基准价水平线 + 实际走势。
 *
 * 说明：这里使用纯 SVG 实现，避免首屏引入额外图表库体积；
 * ECharts 仅在评估/统计页面按需加载（PRD 35.11）。
 */

export interface PredictionOverlayProps {
  /** 基准价水平线。 */
  baseline: number;
  /** 预测区间带：起止时间 + 上下界。 */
  band: {
    startTime: number;
    endTime: number;
    lower: number;
    upper: number;
  };
  /** 实际走势折线（Unix 秒 + 价格）。 */
  actual?: Array<{ time: number; price: number }>;
  /** 图表高度（像素）。 */
  height?: number;
  className?: string;
}

export function PredictionOverlay(props: PredictionOverlayProps) {
  const { baseline, band, actual = [], height = 220, className } = props;

  const width = 640;
  const padding = { top: 16, right: 16, bottom: 24, left: 48 };

  const prices = [baseline, band.lower, band.upper, ...actual.map((p) => p.price)];
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const span = maxPrice - minPrice || 1;

  const allTimes = [band.startTime, band.endTime, ...actual.map((p) => p.time)];
  const minTime = Math.min(...allTimes);
  const maxTime = Math.max(...allTimes);
  const timeSpan = maxTime - minTime || 1;

  const x = (time: number) =>
    padding.left + ((time - minTime) / timeSpan) * (width - padding.left - padding.right);
  const y = (price: number) =>
    padding.top +
    (1 - (price - minPrice) / span) * (height - padding.top - padding.bottom);

  const actualPath = actual
    .map((point, index) => `${index === 0 ? "M" : "L"}${x(point.time)},${y(point.price)}`)
    .join(" ");

  return (
    <div className={className} data-testid="prediction-overlay">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="预测区间与实际走势对照">
        {/* 预测区间带 */}
        <rect
          x={x(band.startTime)}
          y={y(band.upper)}
          width={Math.max(0, x(band.endTime) - x(band.startTime))}
          height={Math.max(0, y(band.lower) - y(band.upper))}
          fill="rgba(148,163,184,0.25)"
          stroke="#94a3b8"
          strokeDasharray="4 3"
        />

        {/* 基准价水平线 */}
        <line
          x1={padding.left}
          x2={width - padding.right}
          y1={y(baseline)}
          y2={y(baseline)}
          stroke="#475569"
          strokeDasharray="6 4"
        />
        <text x={padding.left + 4} y={y(baseline) - 4} fontSize="10" fill="#475569">
          基准 {baseline}
        </text>

        {/* 实际走势 */}
        {actual.length > 0 ? (
          <path d={actualPath} fill="none" stroke="#0f172a" strokeWidth={1.5} />
        ) : null}
      </svg>
    </div>
  );
}