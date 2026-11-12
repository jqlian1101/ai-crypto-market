"use client";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** 推送偏好（含渠道自检，PRD 22.5.7）。 */
export default function NotificationPrefsPage() {
  return (
    <PlaceholderPage
      title="推送偏好"
      description="渠道开关、限流配置、掩码凭据与人工确认项清单。"
      apiPath="/api/v1/notifications/preferences"
      pollSeconds={30}
    />
  );
}