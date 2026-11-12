"use client";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 异动事件列表。 */
export default function AnomaliesPage() {
  return (
    <PlaceholderPage
      title="异动事件"
      description="按规则触发的异动事件及其投递状态。"
      apiPath="/api/v1/anomalies"
      emptyNextStep="本周期无异动记录 —— 可查看异动规则配置。"
      emptyActionLabel="查看异动规则"
      emptyActionHref="/system"
    />
  );
}