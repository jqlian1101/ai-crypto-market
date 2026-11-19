/** 客户端传输重试策略（毫秒），不是业务阈值。 */
export const POLL_BACKOFF_MS = [5_000, 15_000, 60_000] as const;
