"use client";

import { useParams } from "next/navigation";

import { AnalysisReportView } from "@/components/analysis/AnalysisReportView";

/** 分析详情（PRD 8.2）。分析进行中时轮询 3 秒，完成后停止。 */
export default function AnalysisDetailPage() {
  const params = useParams<{ analysisId: string }>();

  return <AnalysisReportView analysisId={params.analysisId} />;
}