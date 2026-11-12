/**
 * 通用数据表格（PRD 第 35.9 节）。
 *
 * 移动端：表格横向滚动 + 首列冻结。
 * 图表必须有**表格替代视图**（可访问性与精确读数），
 * 本组件即用于该目的。
 */

import type { ReactNode } from "react";

export interface DataTableColumn<T> {
  key: string;
  header: string;
  /** 单元格渲染。缺省时按 key 取值。 */
  render?: (row: T, index: number) => ReactNode;
  /** 对齐方式。 */
  align?: "left" | "right" | "center";
  /** 是否为首列（移动端冻结）。 */
  sticky?: boolean;
}

export interface DataTableProps<T> {
  columns: Array<DataTableColumn<T>>;
  rows: T[];
  /** 行 key。 */
  getRowKey: (row: T, index: number) => string;
  caption?: string;
  emptyMessage?: string;
  className?: string;
}

export function DataTable<T>(props: DataTableProps<T>) {
  const { columns, rows, getRowKey, caption, emptyMessage, className } = props;

  if (rows.length === 0) {
    return (
      <p className="py-4 text-center text-sm text-slate-500" data-testid="table-empty">
        {emptyMessage ?? "暂无数据"}
      </p>
    );
  }

  return (
    <div className={`overflow-x-auto ${className ?? ""}`}>
      <table className="w-full min-w-[480px] border-collapse text-sm" data-testid="data-table">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={`px-3 py-2 font-medium ${
                  column.align === "right" ? "text-right" : column.align === "center" ? "text-center" : ""
                } ${column.sticky ? "sticky left-0 z-10 bg-white" : ""}`}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={getRowKey(row, index)} className="border-b border-slate-100">
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={`px-3 py-2 ${
                    column.align === "right"
                      ? "text-right font-mono tabular-nums"
                      : column.align === "center"
                        ? "text-center"
                        : ""
                  } ${column.sticky ? "sticky left-0 z-10 bg-white" : ""}`}
                >
                  {column.render
                    ? column.render(row, index)
                    : String((row as Record<string, unknown>)[column.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}