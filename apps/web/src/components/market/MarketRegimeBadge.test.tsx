import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarketRegimeBadge } from "@/components/market/MarketRegimeBadge";

/**
 * PRD 4.6 / 35.6：多周期不一致时**并列展示多个徽标**。
 */
describe("MarketRegimeBadge", () => {
  it("多周期不一致时并列展示多个徽标", () => {
    render(
      <MarketRegimeBadge
        regimes={["UPTREND", "RANGE"]}
        timeframeLabels={["1h", "4h"]}
      />,
    );
    const badges = screen.getAllByTestId("market-regime-badge");
    expect(badges).toHaveLength(2);
    expect(badges[0]).toHaveAttribute("data-regime", "UPTREND");
    expect(badges[1]).toHaveAttribute("data-regime", "RANGE");
  });

  it("无状态时不渲染", () => {
    const { container } = render(<MarketRegimeBadge regimes={[]} />);
    expect(container.firstChild).toBeNull();
  });
});