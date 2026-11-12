import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SampleSizeBadge } from "@/components/common/SampleSizeBadge";

/**
 * PRD 35.6 / 35.7：样本不足时**不显示任何比率数值**。
 */
describe("SampleSizeBadge", () => {
  it("样本充足时显示 n", () => {
    render(<SampleSizeBadge sampleSize={120} minSampleSize={30} />);
    expect(screen.getByTestId("sample-size")).toHaveTextContent("n=120");
  });

  it("样本不足时显示灰色「样本不足」且不含比率", () => {
    render(<SampleSizeBadge sampleSize={12} minSampleSize={30} />);
    const badge = screen.getByTestId("sample-size-insufficient");
    expect(badge).toHaveTextContent("样本不足");
    expect(badge).toHaveTextContent("12/30");
    expect(badge.textContent).not.toMatch(/%/);
  });

  it("低置信度时带标记", () => {
    render(<SampleSizeBadge sampleSize={120} minSampleSize={30} lowConfidence />);
    expect(screen.getByTestId("sample-size")).toHaveTextContent("低置信度");
  });
});