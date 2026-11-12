"use client";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 实验列表（PRD 8.3）。 */
export default function ExperimentsPage() {
  return (
    <PlaceholderPage
      title="实验"
      description="提示词与参数变体的对比实验。"
      apiPath="/api/v1/experiments"
    />
  );
}