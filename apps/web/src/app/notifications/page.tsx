"use client";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 投递记录与死信。 */
export default function NotificationsPage() {
  return (
    <PlaceholderPage
      title="投递记录"
      description="各渠道的投递流水与死信队列。"
      apiPath="/api/v1/notifications/deliveries"
      pollSeconds={30}
      emptyNextStep="无投递记录 —— 异动或评估报告生成后会触发投递。"
    />
  );
}