/**
 * 访问令牌管理（PRD 第 34.3 / 34.4 节）。
 *
 * - 令牌存 `sessionStorage`（**不是** `localStorage`，减少长期暴露）
 * - 只通过 `Authorization` 头发送，**不放 URL、不放 Cookie**
 * - Dashboard 不显示完整令牌，只显示掩码（前 6 位 + `****`）与指纹
 */

const TOKEN_STORAGE_KEY = "aicrypto.auth.token";

/** 令牌指纹：sha256 前 12 位十六进制。 */
export async function tokenFingerprint(token: string): Promise<string> {
  const bytes = new TextEncoder().encode(token);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 12);
}

/** 掩码令牌：前 6 位 + `****`（PRD 34.4）。 */
export function maskToken(token: string): string {
  if (token.length <= 6) {
    return "****";
  }
  return `${token.slice(0, 6)}****`;
}

export function getStoredToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return window.sessionStorage.getItem(TOKEN_STORAGE_KEY);
}

export function storeToken(token: string): void {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearStoredToken(): void {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.removeItem(TOKEN_STORAGE_KEY);
}
