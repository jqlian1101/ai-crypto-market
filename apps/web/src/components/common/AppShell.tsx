"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

interface NavItem {
  href: string;
  label: string;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

/**
 * 导航结构（对应 PRD 35.2 的三个路由组）。
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    title: "市场",
    items: [
      { href: "/", label: "市场概览" },
      { href: "/opportunities", label: "机会" },
      { href: "/predictions", label: "预测追踪" },
      { href: "/research", label: "研究" },
    ],
  },
  {
    title: "评估",
    items: [
      { href: "/performance", label: "AI 表现" },
      { href: "/agents", label: "Agent 对比" },
      { href: "/experiments", label: "实验" },
    ],
  },
  {
    title: "运维",
    items: [
      { href: "/anomalies", label: "异动事件" },
      { href: "/notifications", label: "投递记录" },
      { href: "/jobs", label: "任务" },
      { href: "/system", label: "系统健康" },
      { href: "/paper", label: "模拟交易" },
    ],
  },
];

export function AppShell(props: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
          <Link href="/" className="text-sm font-semibold tracking-tight">
            AI Crypto Market
          </Link>
          <Link href="/auth/verify" className="text-xs text-slate-600 underline">
            访问令牌
          </Link>
        </div>
      </header>

      <div className="mx-auto flex max-w-7xl gap-6 px-4 py-6">
        <nav className="hidden w-48 shrink-0 md:block" aria-label="主导航">
          {NAV_GROUPS.map((group) => (
            <div key={group.title} className="mb-6">
              <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                {group.title}
              </p>
              <ul className="space-y-0.5">
                {group.items.map((item) => {
                  const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        aria-current={active ? "page" : undefined}
                        className={`block rounded px-2 py-1 text-sm ${
                          active
                            ? "bg-slate-800 font-medium text-white"
                            : "text-slate-600 hover:bg-slate-100"
                        }`}
                      >
                        {item.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <main className="min-w-0 flex-1">{props.children}</main>
      </div>
    </div>
  );
}