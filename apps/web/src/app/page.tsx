"use client";

import Link from "next/link";

import { useHealthQuery, usePollingQuery } from "@/api/queries";
import type { AnalysisReport } from "@/api/schema";
import { ROUTES } from "@/lib/routes";
import { AppShell } from "@/components/common/AppShell";
import { ResourcePage } from "@/components/common/ResourcePage";

export default function HomePage() {
  const health = useHealthQuery();
  const latestAnalysis = usePollingQuery<AnalysisReport | null>({
    queryKey: ["analysis", "latest"],
    path: "/api/v1/analysis/latest",
    intervalMs: 60_000,
  });

  return (
    <AppShell>
      <div className="space-y-6">
        <section>
          <h1 className="text-xl font-semibold tracking-tight">AI 市场分析</h1>
          <p className="mt-1 text-sm text-slate-500">最新综合判断、情景变化与市场证据</p>
        </section>

        <ResourcePage
          query={health}
          emptyReason="后端服务未就绪"
          emptyNextStep="启动后端服务后刷新本页：uvicorn aicrypto.api.app:create_app --factory"
          className="border-y border-slate-200 py-4"
        >
          {(data) => (
            <div className="text-sm text-slate-600">
              <p>
                后端状态：<span className="font-mono">{data.status}</span>
              </p>
              <p className="text-xs text-slate-500">时间戳：{data.timestamp}</p>
            </div>
          )}
        </ResourcePage>

        <ResourcePage
          query={latestAnalysis}
          isEmpty={(report) => report === null}
          emptyReason="尚无已发布的 AI 综合分析"
          emptyNextStep="完成行情快照与分析任务后，最新报告会显示在这里。"
        >
          {(report) =>
            report && (
              <section className="space-y-3 border-y border-slate-200 py-4">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <h2 className="text-base font-semibold">
                    {report.symbol} · {report.content.market_bias}
                  </h2>
                  <span className="text-sm text-slate-500">
                    {report.content.time_horizon} · {report.status}
                  </span>
                  <span className="text-xs text-slate-500">
                    {report.published_at && new Date(report.published_at).toLocaleString()}
                  </span>
                </div>
                <p className="max-w-4xl text-sm leading-6 text-slate-700">
                  {report.content.summary}
                </p>
                {report.content.changes_since_previous ? (
                  <p className="text-sm text-amber-800">
                    相较上次：{report.content.changes_since_previous}
                  </p>
                ) : null}
                {report.content.watch_conditions?.length ? (
                  <p className="text-sm text-slate-600">
                    下一观察：{report.content.watch_conditions.join("；")}
                  </p>
                ) : null}
                <Link
                  className="text-sm font-medium text-sky-700 hover:underline"
                  href={`/analysis/${report.analysis_id}`}
                >
                  查看完整分析
                </Link>
              </section>
            )
          }
        </ResourcePage>

        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
            市场上下文
          </h2>
          <div className="border-y border-slate-200 py-4 text-sm text-slate-500">
            行情、多周期结构及衍生品数据将随分析快照接入。
          </div>
        </section>

        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
            快速入口
          </h2>
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { href: ROUTES.opportunities, label: "机会列表" },
              { href: ROUTES.predictions, label: "预测追踪" },
              { href: ROUTES.anomalies, label: "异动事件" },
              { href: ROUTES.performance, label: "AI 表现" },
            ].map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className="block rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 hover:border-slate-300"
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </AppShell>
  );
}
