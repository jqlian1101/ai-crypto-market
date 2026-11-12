import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SymbolHeader } from "@/components/market/SymbolHeader";

describe("SymbolHeader", () => {
  it("GOOD 时显示价格", () => {
    render(<SymbolHeader symbol="BTCUSDT" price={68000} changePercent={1.25} quality="GOOD" />);
    expect(screen.getByTestId("symbol-price")).toHaveTextContent("68000");
    expect(screen.getByTestId("price-change")).toHaveTextContent("+1.25%");
  });

  it("STALE 时隐藏实时价格", () => {
    render(<SymbolHeader symbol="BTCUSDT" price={68000} changePercent={1.25} quality="STALE" />);
    expect(screen.getByTestId("symbol-price-hidden")).toBeInTheDocument();
    expect(screen.queryByTestId("symbol-price")).not.toBeInTheDocument();
    expect(screen.getByTestId("price-change-hidden")).toBeInTheDocument();
  });

  it("MISSING 时显示「数据缺失」而非 0", () => {
    render(<SymbolHeader symbol="BTCUSDT" price={68000} quality="MISSING" />);
    const hidden = screen.getByTestId("symbol-price-hidden");
    expect(hidden).toHaveTextContent("数据缺失");
    expect(hidden.textContent).not.toContain("0");
  });
});