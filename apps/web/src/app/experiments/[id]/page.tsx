"use client";

import { useParams } from "next/navigation";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 实验详情。 */
export default function ExperimentDetailPage() {
  const params = useParams<{ id: string }>();

  return (
    <PlaceholderPage
      title="实验详情"
      description="实验配置、样本量与评估口径明细。"
      apiPath={`/api/v1/experiments/${params.id}`}
    />
  );
}