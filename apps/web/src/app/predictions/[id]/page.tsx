"use client";

import { useParams } from "next/navigation";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 预测详情 + 后续行情对照。 */
export default function PredictionDetailPage() {
  const params = useParams<{ id: string }>();

  return (
    <PlaceholderPage
      title="预测详情"
      description="预测区间、基准价与后续实际走势对照。"
      apiPath={`/api/v1/predictions/${params.id}`}
    />
  );
}