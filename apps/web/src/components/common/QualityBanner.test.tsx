import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { QualityBanner } from "@/components/common/QualityBanner";

/**
 * PRD 35.5：数据质量必须可见，且禁止项必须被遵守。
 */
describe("QualityBanner", () => {
  it("GOOD 不渲染任何标记", () => {
    const { container } = render(<QualityBanner quality="GOOD" />);
    expect(container.firstChild).toBeNull();
  });

  it("DEGRADED 显示黄色降级提示", () => {
    render(<QualityBanner quality="DEGRADED" />);
    expect(screen.getByTestId("quality-banner-degraded")).toBeInTheDocument();
    expect(screen.getByText("部分数据降级，指标可能不完整")).toBeInTheDocument();
  });

  it("STALE 显示最后更新时间", () => {
    render(<QualityBanner quality="STALE" lastUpdated="2026-01-01T00:00:00Z" />);
    expect(screen.getByTestId("quality-banner-stale")).toBeInTheDocument();
    expect(screen.getByText(/最后更新于/)).toBeInTheDocument();
  });

  it("MISSING 提示数据缺失，不显示数值", () => {
    render(<QualityBanner quality="MISSING" />);
    expect(screen.getByTestId("quality-banner-missing")).toBeInTheDocument();
    expect(screen.getByText(/未显示数值/)).toBeInTheDocument();
  });

  it("INVALID 显示异常提示", () => {
    render(<QualityBanner quality="INVALID" />);
    expect(screen.getByTestId("quality-banner-invalid")).toBeInTheDocument();
    expect(screen.getByText("数据异常，已排除")).toBeInTheDocument();
  });
});