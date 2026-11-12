"use client";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 研究与相似情景。 */
export default function ResearchPage() {
  return (
    <PlaceholderPage
      title="研究"
      description="历史相似情景检索与结构化研究笔记。"
      apiPath="/api/v1/research/similar"
    />
  );
}