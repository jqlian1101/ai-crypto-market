import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EvaluationPanel } from "@/components/evaluation/EvaluationPanel";

/**
 * PRD 35.7：评估页面的强制规则。
 */
describe("EvaluationPanel", () => {
  const group = {
    dimension: "DIRECTION" as const,
    metric_name: "方向命中率",
    group_key: { agent: "alpha" },
    metrics: [
      {
        metric_name: "direction_accuracy",
        value: 0.62,
        sample_size: 40,
        is_insufficient: false,
        confidence: "NORMAL" as const,
        detail: { n_min: 30 },
        metric_value_deduped: 0.58,
      },
    ],
  };

  it("双轨并排展示", () => {
    render(
      <EvaluationPanel
        trackA={{ title: "含成本", groups: [group] }}
        trackB={{ title: "不含成本", groups: [group] }}
      />,
    );
    expect(screen.getAllByTestId("evaluation-track")).toHaveLength(2);
  });

  it("显示去重前后的值", () => {
    render(
      <EvaluationPanel
        trackA={{ title: "含成本", groups: [group] }}
        trackB={{ title: "不含成本", groups: [group] }}
      />,
    );
    expect(screen.getAllByTestId("metric-value")[0]).toHaveTextContent("62.0%");
    expect(screen.getAllByTestId("metric-value")[0]).toHaveTextContent("58.0%");
  });

  it("样本不足时显示 — 而非百分比", () => {
    const insufficientGroup = {
      ...group,
      metrics: [
        {
          ...group.metrics[0]!,
          value: null,
          sample_size: 5,
          confidence: "INSUFFICIENT_SAMPLE" as const,
          detail: { n_min: 30 },
        },
      ],
    };
    render(
      <EvaluationPanel
        trackA={{ title: "含成本", groups: [insufficientGroup] }}
        trackB={{ title: "不含成本", groups: [insufficientGroup] }}
      />,
    );
    const values = screen.getAllByTestId("metric-insufficient");
    expect(values).toHaveLength(2);
    expect(values[0]?.textContent).toBe("—");
    expect(values[0]?.textContent).not.toContain("%");
  });

  it("顶部展示口径版本", () => {
    render(
      <EvaluationPanel
        trackA={{ title: "A", groups: [] }}
        trackB={{ title: "B", groups: [] }}
        evaluatorVersion="v2.3.0"
        evaluatorEffectiveFrom="2026-01-01"
      />,
    );
    expect(screen.getByTestId("evaluator-version")).toHaveTextContent("v2.3.0");
  });
});