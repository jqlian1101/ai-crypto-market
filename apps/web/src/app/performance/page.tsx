"use client";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** AI 表现（双轨并排，PRD 25.5）。 */
export default function PerformancePage() {
  return (
    <PlaceholderPage
      title="AI 表现"
      description="双轨评估结果：方向命中率、净盈亏、四象限交叉表。"
      apiPath="/api/v1/evaluation/report"
      emptyNextStep="评估为重计算任务，生成后可在此查看。"
    />
  );
}