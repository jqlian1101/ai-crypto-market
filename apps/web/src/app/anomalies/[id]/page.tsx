"use client";

import { useParams } from "next/navigation";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 异动详情（含各 revision 的投递记录）。 */
export default function AnomalyDetailPage() {
  const params = useParams<{ id: string }>();

  return (
    <PlaceholderPage
      title="异动详情"
      description="规则 ID、触发阈值、实测值与各 revision 的投递记录。"
      apiPath={`/api/v1/anomalies/${params.id}`}
      pollSeconds={30}
    />
  );
}