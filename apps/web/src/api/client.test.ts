import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { AxiosError, AxiosHeaders } from "axios";

import { ApiRequestError, apiGet, apiRequest } from "./client";

/**
 * axios 客户端行为测试（PRD 第 31 章）。
 */
describe("api client", () => {
  let requestSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    requestSpy = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("解包 { data, meta } 并带出 request_id", async () => {
    const { apiClient } = await import("./client");
    requestSpy.mockResolvedValue({
      status: 200,
      headers: new AxiosHeaders({ etag: 'W/"abc"' }),
      data: {
        data: { value: 42 },
        meta: { request_id: "req-1", trace_id: "trace-1", generated_at: "2026-01-01T00:00:00Z" },
      },
    });
    vi.spyOn(apiClient, "request").mockImplementation(requestSpy as never);

    const result = await apiGet<{ value: number }>("/api/v1/test");

    expect(result.data).toEqual({ value: 42 });
    expect(result.requestId).toBe("req-1");
    expect(result.traceId).toBe("trace-1");
    expect(result.etag).toBe('W/"abc"');
    expect(result.notModified).toBe(false);
  });

  it("304 时标记 notModified 且不重传数据体", async () => {
    const { apiClient } = await import("./client");
    requestSpy.mockResolvedValue({
      status: 304,
      headers: new AxiosHeaders(),
      data: "",
    });
    vi.spyOn(apiClient, "request").mockImplementation(requestSpy as never);

    const result = await apiGet("/api/v1/test");

    expect(result.notModified).toBe(true);
    expect(result.data).toBeNull();
  });

  it("条件请求带上 If-None-Match / If-Modified-Since", async () => {
    const { apiClient } = await import("./client");
    requestSpy.mockResolvedValue({
      status: 200,
      headers: new AxiosHeaders(),
      data: { data: {}, meta: { request_id: "r", generated_at: "2026-01-01T00:00:00Z" } },
    });
    vi.spyOn(apiClient, "request").mockImplementation(requestSpy as never);

    await apiRequest("/api/v1/test", { etag: 'W/"x"', lastModified: "Wed, 01 Jan 2026 00:00:00 GMT" });

    const config = requestSpy.mock.calls[0]?.[0];
    expect(config.headers["If-None-Match"]).toBe('W/"x"');
    expect(config.headers["If-Modified-Since"]).toBe("Wed, 01 Jan 2026 00:00:00 GMT");
  });

  it("后端错误码被映射为 ApiRequestError", async () => {
    const { apiClient } = await import("./client");
    requestSpy.mockRejectedValue(
      new AxiosError("Request failed", undefined, undefined, undefined, {
        status: 404,
        data: {
          error: {
            code: "COMMON_NOT_FOUND",
            message: "资源不存在",
            details: {},
            retryable: false,
            request_id: "req-404",
          },
        },
        headers: {},
      } as never),
    );
    vi.spyOn(apiClient, "request").mockImplementation(requestSpy as never);

    await expect(apiGet("/api/v1/missing")).rejects.toThrowError(ApiRequestError);

    try {
      await apiGet("/api/v1/missing");
    } catch (error) {
      const apiError = error as ApiRequestError;
      expect(apiError.code).toBe("COMMON_NOT_FOUND");
      expect(apiError.status).toBe(404);
      expect(apiError.requestId).toBe("req-404");
      expect(apiError.retryable).toBe(false);
    }
  });

  it("网络失败标记为可重试", async () => {
    const { apiClient } = await import("./client");
    requestSpy.mockRejectedValue(new AxiosError("Network Error", "ERR_NETWORK"));
    vi.spyOn(apiClient, "request").mockImplementation(requestSpy as never);

    try {
      await apiGet("/api/v1/test");
      expect.unreachable("应当抛出");
    } catch (error) {
      const apiError = error as ApiRequestError;
      expect(apiError.code).toBe("COMMON_SERVICE_UNAVAILABLE");
      expect(apiError.retryable).toBe(true);
    }
  });
});