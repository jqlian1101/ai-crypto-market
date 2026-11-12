"use client";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 任务与运行历史。 */
export default function JobsPage() {
  return (
    <PlaceholderPage
      title="任务"
      description="定时任务与手动任务的运行历史。"
      apiPath="/api/v1/jobs/runs"
      pollSeconds={30}
    />
  );
}