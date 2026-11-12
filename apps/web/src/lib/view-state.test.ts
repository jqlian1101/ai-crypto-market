import { describe, expect, it } from "vitest";

import { deriveViewState, isExpired } from "@/lib/view-state";

/**
 * PRD 35.4：六态推导规则。
 */
describe("deriveViewState", () => {
  const pending = { isPending: true, isError: false, data: undefined, error: null };
  const ready = { isPending: false, isError: false, data: { a: 1 }, error: null };
  const errored = { isPending: false, isError: true, data: undefined, error: new Error("x") };

  it("错误优先于其他状态", () => {
    expect(deriveViewState(errored)).toBe("ERROR");
  });

  it("首次加载为 LOADING", () => {
    expect(deriveViewState(pending)).toBe("LOADING");
  });

  it("有数据为 READY", () => {
    expect(deriveViewState(ready)).toBe("READY");
  });

  it("查询成功但无数据为 EMPTY", () => {
    expect(deriveViewState(ready, { isEmpty: true })).toBe("EMPTY");
  });

  it("数据质量降级为 DEGRADED 且仍有内容", () => {
    expect(deriveViewState(ready, { quality: "DEGRADED" })).toBe("DEGRADED");
  });

  it("超过 TTL 为 STALE", () => {
    const past = new Date(Date.now() - 10_000).toISOString();
    expect(deriveViewState(ready, { lastUpdated: past, ttlSeconds: 5 })).toBe("STALE");
  });

  it("isExpired 判定过期", () => {
    const past = new Date(Date.now() - 10_000).toISOString();
    expect(isExpired(past, 5)).toBe(true);
    expect(isExpired(new Date().toISOString(), 5)).toBe(false);
  });
});