import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EvidenceBlock } from "@/components/analysis/EvidenceBlock";

/**
 * PRD 35.6：支持证据与反对证据**必须区分显示**。
 */
describe("EvidenceBlock", () => {
  it("同时显示支持与反对证据", () => {
    render(
      <EvidenceBlock
        supporting={[{ text: "放量突破", source: "volume" }]}
        opposing={[{ text: "资金费率偏高", source: "funding" }]}
      />,
    );
    expect(screen.getByTestId("evidence-column-up")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-column-down")).toBeInTheDocument();
    expect(screen.getByText("放量突破")).toBeInTheDocument();
    expect(screen.getByText("资金费率偏高")).toBeInTheDocument();
  });

  it("反对证据为空时给出明确说明", () => {
    render(<EvidenceBlock supporting={[{ text: "x" }]} opposing={[]} />);
    // 支持侧有 1 条，反对侧为空 → 仅反对侧出现空态提示
    expect(screen.getAllByTestId("evidence-empty")).toHaveLength(1);
    expect(screen.getByTestId("evidence-column-down")).toHaveTextContent("本次分析未提供反对证据。");
  });
});