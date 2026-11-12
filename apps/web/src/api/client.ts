/**
 * API 请求封装（PRD 第 31 章）。
 *
 * 基于 axios：
 * - 统一解包 `{ data, meta }`（31.2）
 * - 错误统一抛出 `ApiRequestError`，携带后端的**稳定错误码**（31.6）
 * - 支持条件请求（`ETag` / `Last-Modified`），服务端返回 304 时不重传数据体（35.3 规则 2）
 *
 * 注意：axios 的 `response.data` 与我们约定的响应体字段名冲突，
 * 因此统一使用 `AxiosResponse<ApiSuccess<T>>` 并显式读取 `response.data.data`。
 */

import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import { getStoredToken } from "./auth";
import type { ApiError, ApiSuccess } from "./schema";

/** 统一错误类型。`code` 是后端登记的错误码，**禁止**用自由文本判断。 */
export class ApiRequestError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;
  readonly retryable: boolean;
  readonly requestId: string;

  constructor(params: {
    code: string;
    message: string;
    status: number;
    details?: Record<string, unknown>;
    retryable?: boolean;
    requestId?: string;
  }) {
    super(params.message);
    this.name = "ApiRequestError";
    this.code = params.code;
    this.status = params.status;
    this.details = params.details ?? {};
    this.retryable = params.retryable ?? false;
    this.requestId = params.requestId ?? "";
  }

  /** 是否为鉴权失败（前端应跳转到令牌页）。 */
  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}

export interface RequestOptions {
  /** 请求体（对象将自动 JSON 序列化）。 */
  body?: unknown;
  /** 条件请求：上次响应的 ETag。 */
  etag?: string | null;
  /** 条件请求：上次响应的 Last-Modified。 */
  lastModified?: string | null;
  /** 附加请求头。 */
  headers?: Record<string, string>;
  /** 查询参数。 */
  params?: Record<string, unknown>;
  /** 覆盖 HTTP 方法。 */
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** axios 请求配置透传。 */
  signal?: AbortSignal;
  /** 覆盖超时（毫秒）。 */
  timeout?: number;
}

export interface ApiResult<T> {
  data: T;
  requestId: string;
  traceId: string | null;
  generatedAt: string;
  /** 服务端返回 304 时为 true，此时 `data` 为 null。 */
  notModified: boolean;
  etag: string | null;
  lastModified: string | null;
}

/**
 * axios 实例。
 *
 * - `baseURL` 为空：使用同源相对路径，由 Next.js rewrites 代理到后端（35.11）
 * - 请求拦截器注入 `Authorization` 头（34.3/34.4）
 * - `validateStatus` 接受 304，用于条件请求
 */
export const apiClient: AxiosInstance = axios.create({
  baseURL: undefined,
  timeout: 30_000,
  withCredentials: false,
  headers: {
    Accept: "application/json",
  },
  validateStatus: (status) => status === 304 || (status >= 200 && status < 300),
});

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  // axios v1 的 config.headers 是 AxiosHeaders 实例，用 set() 追加而非整体替换，
  // 以免丢掉实例级默认头（如 Accept）。
  const token = getStoredToken();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

/**
 * 发起 API 请求并解包统一响应。
 *
 * @throws {ApiRequestError} 非 2xx 或网络失败
 */
export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<ApiResult<T>> {
  const { body, etag, lastModified, headers, params, method = "GET", signal, timeout } = options;

  const requestHeaders: Record<string, string> = { ...(headers ?? {}) };
  if (etag) {
    requestHeaders["If-None-Match"] = etag;
  }
  if (lastModified) {
    requestHeaders["If-Modified-Since"] = lastModified;
  }
  if (body !== undefined && !requestHeaders["Content-Type"]) {
    requestHeaders["Content-Type"] = "application/json";
  }

  let response: AxiosResponse<ApiSuccess<T>>;
  try {
    response = await apiClient.request<ApiSuccess<T>>({
      url: path,
      method,
      params,
      headers: requestHeaders,
      data: body,
      signal,
      timeout,
    });
  } catch (cause) {
    throw toApiRequestError(cause);
  }

  const responseEtag = headerOf(response, "etag");
  const responseLastModified = headerOf(response, "last-modified");

  if (response.status === 304) {
    return {
      data: null as T,
      requestId: headerOf(response, "x-request-id") ?? "",
      traceId: headerOf(response, "x-trace-id") ?? null,
      generatedAt: new Date().toISOString(),
      notModified: true,
      etag: responseEtag,
      lastModified: responseLastModified,
    };
  }

  const payload = response.data;
  return {
    data: payload.data,
    requestId: payload.meta.request_id,
    traceId: payload.meta.trace_id ?? null,
    generatedAt: payload.meta.generated_at,
    notModified: false,
    etag: responseEtag,
    lastModified: responseLastModified,
  };
}

/** 读取响应头（大小写不敏感）。 */
function headerOf(response: AxiosResponse, name: string): string | null {
  const value = response.headers?.[name];
  if (typeof value === "string") {
    return value;
  }
  if (value !== undefined && typeof value === "object") {
    return String((value as { value?: string }).value ?? "") || null;
  }
  return null;
}

/** axios 错误 → `ApiRequestError`。 */
function toApiRequestError(cause: unknown): ApiRequestError {
  if (cause instanceof AxiosError) {
    const status = cause.response?.status ?? 0;

    if (!cause.response) {
      // 网络层失败（超时、断连、取消）
      return new ApiRequestError({
        code: "COMMON_SERVICE_UNAVAILABLE",
        message: cause.code === "ECONNABORTED" ? "请求超时" : "网络请求失败",
        status: 503,
        retryable: true,
        requestId: "",
      });
    }

    const payload = cause.response.data as ApiError | undefined;
    const error = payload?.error;

    // 网关级失败（后端未启动 / 超时 / 反代失败）且没有结构化错误体时，
    // 归为服务不可用，而不是内部错误。
    if (!error) {
      const isGateway = status === 502 || status === 503 || status === 504;
      return new ApiRequestError({
        code: isGateway ? "COMMON_SERVICE_UNAVAILABLE" : "COMMON_INTERNAL_ERROR",
        message: isGateway ? "后端服务不可用" : `请求失败（HTTP ${status}）`,
        status,
        retryable: isGateway || status >= 500,
        requestId: headerOf(cause.response, "x-request-id") ?? "",
      });
    }

    return new ApiRequestError({
      code: error.code ?? "COMMON_INTERNAL_ERROR",
      message: error.message ?? `请求失败（HTTP ${status}）`,
      status,
      details: error.details ?? {},
      retryable: error.retryable ?? status >= 500,
      requestId: error.request_id ?? headerOf(cause.response, "x-request-id") ?? "",
    });
  }

  return new ApiRequestError({
    code: "COMMON_INTERNAL_ERROR",
    message: cause instanceof Error ? cause.message : "未知错误",
    status: 500,
    retryable: true,
    requestId: "",
  });
}

export function apiGet<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  return apiRequest<T>(path, { ...options, method: "GET" });
}

export function apiPost<T>(
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<ApiResult<T>> {
  return apiRequest<T>(path, { ...options, method: "POST", body });
}

export function apiPut<T>(
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<ApiResult<T>> {
  return apiRequest<T>(path, { ...options, method: "PUT", body });
}

export function apiPatch<T>(
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<ApiResult<T>> {
  return apiRequest<T>(path, { ...options, method: "PATCH", body });
}

export function apiDelete<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  return apiRequest<T>(path, { ...options, method: "DELETE" });
}
