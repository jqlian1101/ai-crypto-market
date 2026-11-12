"use client";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 系统健康与事件。 */
export default function SystemPage() {
  return (
    <PlaceholderPage
      title="系统健康"
      description="依赖探针、异动规则配置与系统事件流。"
      apiPath="/api/v1/system/events"
      pollSeconds={30}
    />
  );
}