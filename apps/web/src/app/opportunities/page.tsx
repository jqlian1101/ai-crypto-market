"use client";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 机会列表。 */
export default function OpportunitiesPage() {
  return (
    <PlaceholderPage
      title="机会"
      description="按多周期一致性筛选出的交易机会候选。"
      apiPath="/api/v1/opportunities"
      emptyNextStep="机会由分析任务产出，可在「任务」页查看生成进度。"
    />
  );
}