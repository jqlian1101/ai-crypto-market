"use client";

import { useMutation } from "@tanstack/react-query";

import { apiPost } from "@/api/client";
import { useStaticQuery } from "@/api/queries";
import type { AnalysisReport } from "@/api/schema";
import { EvidenceBlock } from "@/components/analysis/EvidenceBlock";
import { AppShell } from "@/components/common/AppShell";
import { ResourcePage } from "@/components/common/ResourcePage";

export function AnalysisReportView({ analysisId }: { analysisId: string }) {
  const path = `/api/v1/analysis/${encodeURIComponent(analysisId)}`;
  const query = useStaticQuery<AnalysisReport>(["analysis", analysisId], path);

  return (
    <AppShell>
      <div className="space-y-5">
        <section>
          <h1 className="text-xl font-semibold">AI 综合分析</h1>
          <p className="mt-1 text-sm text-slate-500">报告、证据与后续观察条件</p>
        </section>
        <ResourcePage
          query={query}
          emptyReason="报告尚未生成"
          emptyNextStep="运行状态完成后刷新报告。"
        >
          {(report) => (
            <>
              <ReportContent report={report} />
              <ReportFeedback key={analysisId} path={path} refresh={() => void query.refetch()} />
              {report.feedback_summary ? (
                <p className="mt-2 text-sm text-slate-500">
                  {report.feedback_summary.useful_count} 人认为有用 ·{" "}
                  {report.feedback_summary.not_useful_count} 人认为无用
                </p>
              ) : null}
            </>
          )}
        </ResourcePage>
      </div>
    </AppShell>
  );
}

function ReportFeedback({ path, refresh }: { path: string; refresh: () => void }) {
  const vote = useMutation({
    mutationFn: ({ useful, key }: { useful: boolean; key: string }) =>
      apiPost(`${path}/feedback`, { useful }, { headers: { "Idempotency-Key": key } }),
    onSuccess: refresh,
  });
  const submit = (useful: boolean) => {
    const key = vote.variables?.useful === useful ? vote.variables.key : crypto.randomUUID();
    vote.mutate({ useful, key });
  };

  return (
    <section className="mt-5 border-t border-slate-200 pt-4" aria-label="报告反馈">
      <p className="mb-2 text-sm font-semibold">这份报告对你有帮助吗？</p>
      <div className="flex gap-2">
        <button
          type="button"
          className="rounded border px-3 py-1 text-sm disabled:opacity-50"
          disabled={vote.isPending || vote.isSuccess}
          onClick={() => submit(true)}
        >
          有帮助
        </button>
        <button
          type="button"
          className="rounded border px-3 py-1 text-sm disabled:opacity-50"
          disabled={vote.isPending || vote.isSuccess}
          onClick={() => submit(false)}
        >
          没有帮助
        </button>
      </div>
      {vote.isSuccess ? (
        <p role="status" className="mt-2 text-sm">
          已收到反馈
        </p>
      ) : null}
      {vote.error ? (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {vote.error.message}
        </p>
      ) : null}
    </section>
  );
}

function ReportContent({ report }: { report: AnalysisReport }) {
  const { content } = report;
  const {
    scenarios = [],
    timeframe_views = {},
    evidence = [],
    counter_evidence = [],
    disagreements = [],
  } = content;
  return (
    <article className="space-y-5" data-testid="analysis-report">
      <header className="border-b border-slate-200 pb-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <strong>{report.symbol}</strong>
          <span>{report.market_type}</span>
          <span>{content.time_horizon}</span>
          <span>{report.analysis_mode}</span>
          <span>{report.status}</span>
          {report.degraded ? (
            <span className="text-amber-700">部分降级：{report.degraded_reason}</span>
          ) : null}
        </div>
        <p className="mt-2 text-xs text-slate-500">
          快照截止 {new Date(report.snapshot_as_of).toLocaleString()} · 发布{" "}
          {report.published_at ? new Date(report.published_at).toLocaleString() : "尚未发布"}
        </p>
      </header>

      <section>
        <h2 className="text-sm font-semibold">主要判断：{content.market_bias}</h2>
        <p className="mt-2 text-sm leading-6 text-slate-700">{content.summary}</p>
      </section>

      {content.changes_since_previous ? (
        <section className="border-l-2 border-amber-500 pl-3">
          <h2 className="text-sm font-semibold">相较上次的变化</h2>
          <p className="mt-1 text-sm text-slate-700">{content.changes_since_previous}</p>
        </section>
      ) : null}

      <section>
        <h2 className="mb-2 text-sm font-semibold">情景分析</h2>
        <div className="divide-y divide-slate-200 border-y border-slate-200">
          {scenarios.map((scenario) => (
            <div key={scenario.name} className="py-3">
              <div className="flex flex-wrap justify-between gap-2 text-sm font-medium">
                <span>{scenario.name}</span>
                <span>
                  {scenario.time_horizon}
                  {scenario.probability == null
                    ? ""
                    : ` · ${(scenario.probability * 100).toFixed(0)}%`}
                </span>
              </div>
              <p className="mt-1 text-sm text-slate-700">{scenario.description}</p>
              {scenario.trigger_conditions?.length ? (
                <p className="mt-1 text-xs text-emerald-800">
                  触发：{scenario.trigger_conditions.join("；")}
                </p>
              ) : null}
              {scenario.invalidation_conditions?.length ? (
                <p className="mt-1 text-xs text-red-800">
                  失效：{scenario.invalidation_conditions.join("；")}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold">多周期观点</h2>
        <dl className="grid gap-x-4 gap-y-2 sm:grid-cols-2">
          {Object.entries(timeframe_views).map(([frame, view]) => (
            <div key={frame} className="border-b border-slate-100 py-2">
              <dt className="text-xs font-medium text-slate-500">{frame}</dt>
              <dd className="mt-1 text-sm">{view}</dd>
            </div>
          ))}
        </dl>
      </section>

      <EvidenceBlock
        supporting={evidence.map((item) => ({
          text: item.interpretation || `${item.source_path}: ${item.value ?? ""}`,
          source: item.source_path,
        }))}
        opposing={counter_evidence.map((item) => ({
          text: item.interpretation || `${item.source_path}: ${item.value ?? ""}`,
          source: item.source_path,
        }))}
      />

      <div className="grid gap-5 md:grid-cols-2">
        <ListSection title="下一步观察" items={content.watch_conditions} />
        <ListSection title="失效条件" items={content.invalidation_conditions} />
        <ListSection title="关键价位" items={content.key_levels} />
        <ListSection title="不确定性" items={content.uncertainties} />
      </div>
      {disagreements.length ? <ListSection title="Agent 分歧" items={disagreements} /> : null}
    </article>
  );
}

function ListSection({ title, items = [] }: { title: string; items?: string[] }) {
  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold">{title}</h2>
      {items.length ? (
        <ul className="list-inside list-disc space-y-1 text-sm text-slate-700">
          {items.map((item, index) => (
            <li key={`${index}-${item}`}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-slate-500">暂无内容</p>
      )}
    </section>
  );
}
