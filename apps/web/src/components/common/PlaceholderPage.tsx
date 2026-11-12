"use client";

import { usePollingQuery } from "@/api/queries";
import { AppShell } from "@/components/common/AppShell";
import { ResourcePage } from "@/components/common/ResourcePage";

export interface PlaceholderPageProps {
  title: string;
  description: string;
  /** 后端端点路径。端点未落地时后端返回 404，前端按 ERROR 态呈现（含 request_id）。 */
  apiPath: string;
  /** 轮询间隔（秒）。 */
  pollSeconds?: number;
  /** EMPTY 态的下一步动作。 */
  emptyNextStep?: string;
  /** EMPTY 态的下一步动作链接。 */
  emptyActionHref?: string;
  /** EMPTY 态的下一步动作文案。 */
  emptyActionLabel?: string;
}

/**
 * 占位页面。
 *
 * 后端业务端点尚未落地时，本页面对目标端点发起真实请求：
 * - 端点存在 → 渲染数据
 * - 端点未落地 → 后端返回 404，前端进入 ERROR 态并显示 request_id（可排障）
 *
 * 这样保证**六种状态全部被显式实现**，而不是渲染一个假的空页面。
 */
export function PlaceholderPage(props: PlaceholderPageProps) {
  const { title, description, apiPath, pollSeconds = 60, emptyNextStep, emptyActionHref, emptyActionLabel } =
    props;

  const query = usePollingQuery<unknown>({
    queryKey: ["placeholder", apiPath],
    path: apiPath,
    intervalMs: pollSeconds * 1000,
  });

  return (
    <AppShell>
      <div className="space-y-6">
        <section>
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-1 text-sm text-slate-500">{description}</p>
        </section>

        <ResourcePage
          query={query}
          emptyReason="该接口返回了空结果"
          emptyNextStep={emptyNextStep}
          emptyActionHref={emptyActionHref}
          emptyActionLabel={emptyActionLabel}
          className="rounded border border-slate-200 bg-white p-4"
        >
          {() => (
            <pre className="overflow-x-auto text-xs text-slate-600">
              {JSON.stringify(query.data, null, 2)}
            </pre>
          )}
        </ResourcePage>
      </div>
    </AppShell>
  );
}