import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StateBoundary } from "@/components/common/StateBoundary";

/**
 * 覆盖 PRD 35.4 的六种状态渲染。
 */
describe("StateBoundary", () => {
  it("LOADING 渲染骨架屏", () => {
    render(<StateBoundary state="LOADING" />);
    expect(screen.getByTestId("state-loading")).toBeInTheDocument();
    expect(screen.getByTestId("skeleton")).toBeInTheDocument();
  });

  it("READY 渲染内容", () => {
    render(
      <StateBoundary state="READY">
        <p>内容</p>
      </StateBoundary>,
    );
    expect(screen.getByTestId("state-ready")).toBeInTheDocument();
    expect(screen.getByText("内容")).toBeInTheDocument();
  });

  it("EMPTY 渲染空态内容", () => {
    render(<StateBoundary state="EMPTY" empty={<p>暂无数据</p>} />);
    expect(screen.getByTestId("state-empty")).toBeInTheDocument();
    expect(screen.getByText("暂无数据")).toBeInTheDocument();
  });

  it("STALE 仍渲染内容（不降级为空态）", () => {
    render(
      <StateBoundary state="STALE">
        <p>内容</p>
      </StateBoundary>,
    );
    expect(screen.getByTestId("state-stale")).toBeInTheDocument();
    expect(screen.getByText("内容")).toBeInTheDocument();
  });

  it("DEGRADED 仍渲染内容", () => {
    render(
      <StateBoundary state="DEGRADED">
        <p>内容</p>
      </StateBoundary>,
    );
    expect(screen.getByTestId("state-degraded")).toBeInTheDocument();
    expect(screen.getByText("内容")).toBeInTheDocument();
  });

  it("ERROR 渲染错误内容", () => {
    render(<StateBoundary state="ERROR" error={<p>加载失败</p>} />);
    expect(screen.getByTestId("state-error")).toBeInTheDocument();
    expect(screen.getByText("加载失败")).toBeInTheDocument();
  });
});