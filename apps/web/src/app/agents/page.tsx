"use client";

import { PlaceholderPage } from "@/components/common/PlaceholderPage";

/** Agent 对比。 */
export default function AgentsPage() {
  return (
    <PlaceholderPage
      title="Agent 对比"
      description="各分析 Agent 的表现与可靠性对比。"
      apiPath="/api/v1/agents"
    />
  );
}