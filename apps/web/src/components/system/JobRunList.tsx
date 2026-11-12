/**
 * 任务运行历史列表（PRD 第 35.6 节 / 35.3 表格最后一行）。
 *
 * 运行中 → 轮询；完成后 30 秒轮询。
 */

import type { DataTableColumn } from "@/components/common/DataTable";
import { DataTable } from "@/components/common/DataTable";

export interface JobRun {
  runId: string;
  jobName: string;
  status: "RUNNING" | "SUCCESS" | "FAILED" | "SKIPPED" | string;
  startedAt: string;
  finishedAt?: string | null;
  durationMs?: number | null;
  errorMessage?: string | null;
}

export interface JobRunListProps {
  runs: JobRun[];
  className?: string;
}

export function JobRunList(props: JobRunListProps) {
  const { runs, className } = props;

  const columns: Array<DataTableColumn<JobRun>> = [
    { key: "jobName", header: "任务", sticky: true },
    { key: "status", header: "状态", render: (row) => <JobStatus status={row.status} /> },
    { key: "startedAt", header: "开始", render: (row) => <span className="font-mono text-xs">{row.startedAt}</span> },
    {
      key: "durationMs",
      header: "耗时",
      align: "right",
      render: (row) => (row.durationMs === null || row.durationMs === undefined ? "—" : `${row.durationMs} ms`),
    },
    {
      key: "errorMessage",
      header: "错误",
      render: (row) =>
        row.errorMessage ? <span className="text-xs text-red-700">{row.errorMessage}</span> : <span className="text-slate-400">—</span>,
    },
  ];

  return (
    <div className={className}>
      <DataTable<JobRun>
        caption="任务运行历史"
        columns={columns}
        rows={runs}
        getRowKey={(row) => row.runId}
      />
    </div>
  );
}

function JobStatus(props: { status: string }) {
  const { status } = props;
  const style =
    status === "SUCCESS"
      ? "bg-emerald-50 text-emerald-800"
      : status === "FAILED"
        ? "bg-red-50 text-red-800"
        : status === "RUNNING"
          ? "bg-sky-50 text-sky-800"
          : "bg-slate-100 text-slate-600";

  return (
    <span className={`rounded px-2 py-0.5 text-xs ${style}`} data-testid="job-status">
      {status}
    </span>
  );
}