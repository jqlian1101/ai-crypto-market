"use client";

import { useParams } from "next/navigation";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 单资产详情（多周期 + 结构 + 衍生品 + 流动性）。 */
export default function MarketDetailPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = params.symbol;

  return (
    <PlaceholderPage
      title={`单资产详情 · ${symbol}`}
      description="多周期 K 线与指标、市场结构、衍生品与流动性分布。"
      apiPath={`/api/v1/market/overview?symbol=${encodeURIComponent(symbol)}`}
    />
  );
}