"use client";

import Link from "next/link";

import { useHealthQuery } from "@/api/queries";
import { ROUTES } from "@/api/types";
import { AppShell } from "@/components/common/AppShell";
import { ResourcePage } from "@/components/common/ResourcePage";

/**
 * 首页 = Market Overview + AI 分析（PRD 第 8.1 节）。
 *
 * 布局：> 1024px 时 市场概览 2/3 + 分析结论 1/3（PRD 35.9）。
 */
export default function HomePage() {
  const health = useHealthQuery();

  return (
    <AppShell>
      <div className="space-y-6">
        <section>
          <h1 className="text-xl font-semibold tracking-tight">市场概览</h1>
          <p className="mt-1 text-sm text-slate-500">
            多周期市场状态、AI 分析结论、机会与预测追踪。
          </p>
        </section>

        <ResourcePage
          query={health}
          emptyReason="后端服务未就绪"
          emptyNextStep="启动后端服务后刷新本页：uvicorn aicrypto.api.app:create_app --factory"
          className="rounded border border-slate-200 bg-white p-4"
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

        {/* 三个区块：市场概览 2/3 + 分析结论 1/3 */}
        <div className="grid gap-6 lg:grid-cols-3">
          <section className="lg:col-span-2">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
              市场概览
            </h2>
            <div className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-500">
              行情区块等待后端 `/api/v1/market/**` 端点落地后接入。
            </div>
          </section>

          <section>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
              分析结论
            </h2>
            <div className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-500">
              AI 结论区块等待后端分析端点落地后接入。
            </div>
          </section>
        </div>

        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">快速入口</h2>
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