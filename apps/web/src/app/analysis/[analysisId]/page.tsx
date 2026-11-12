"use client";

import { useParams } from "next/navigation";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 分析详情（PRD 8.2）。分析进行中时轮询 3 秒，完成后停止。 */
export default function AnalysisDetailPage() {
  const params = useParams<{ analysisId: string }>();

  return (
    <PlaceholderPage
      title="分析详情"
      description="AI 分析结论、证据链、置信度与后续行情对照。"
      apiPath={`/api/v1/analysis/${params.analysisId}`}
      pollSeconds={3}
    />
  );
}