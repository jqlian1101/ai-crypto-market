"use client";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 模拟交易（PRD 8.4，P1）。 */
export default function PaperPage() {
  return (
    <PlaceholderPage
      title="模拟交易"
      description="dry-run 模式下的模拟持仓与权益曲线。"
      apiPath="/api/v1/paper/positions"
    />
  );
}