"use client";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 预测追踪（PRD 8.1 第 5 区）。 */
export default function PredictionsPage() {
  return (
    <PlaceholderPage
      title="预测追踪"
      description="历史预测及其到期后的评估结果。"
      apiPath="/api/v1/predictions"
      emptyNextStep="无已到期预测 —— 预测将在窗口结束后自动评估。"
    />
  );
}