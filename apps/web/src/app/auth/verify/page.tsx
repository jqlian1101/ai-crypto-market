"use client";

import { useState } from "react";

import { clearStoredToken, maskToken, storeToken, tokenFingerprint } from "@/api/auth";
import { AppShell } from "@/components/common/AppShell";

/**
 * 令牌输入页（PRD 第 34.4 节）。
 *
 * - 令牌存 `sessionStorage`，只通过 `Authorization` 头发送
 * - 页面显示**掩码**令牌与指纹，不显示完整令牌
 */
export default function AuthVerifyPage() {
  const [token, setToken] = useState("");
  const [fingerprint, setFingerprint] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const handleSave = async () => {
    const trimmed = token.trim();
    if (!trimmed) {
      return;
    }
    storeToken(trimmed);
    setFingerprint(await tokenFingerprint(trimmed));
    setSaved(maskToken(trimmed));
    setToken("");
  };

  const handleClear = () => {
    clearStoredToken();
    setFingerprint(null);
    setSaved(null);
  };

  return (
    <AppShell>
      <div className="mx-auto max-w-md space-y-6">
        <section>
          <h1 className="text-xl font-semibold tracking-tight">访问令牌</h1>
          <p className="mt-1 text-sm text-slate-500">
            令牌保存在当前标签页会话中（sessionStorage），不会写入 localStorage，也不会出现在 URL 中。
          </p>
        </section>

        <section className="rounded border border-slate-200 bg-white p-4">
          <label htmlFor="token" className="block text-sm font-medium">
            API 访问令牌
          </label>
          <input
            id="token"
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="粘贴令牌"
            className="mt-2 w-full rounded border border-slate-300 px-3 py-2 font-mono text-sm focus:border-slate-500 focus:outline-none"
          />
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={() => void handleSave()}
              className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white hover:bg-slate-700"
            >
              保存
            </button>
            <button
              type="button"
              onClick={handleClear}
              className="rounded bg-slate-100 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-200"
            >
              清除
            </button>
          </div>

          {saved ? (
            <dl className="mt-4 space-y-1 border-t border-slate-100 pt-3 text-xs">
              <div className="flex justify-between">
                <dt className="text-slate-500">已保存令牌</dt>
                <dd className="font-mono" data-testid="masked-token">
                  {saved}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-500">指纹</dt>
                <dd className="font-mono" data-testid="token-fingerprint">
                  {fingerprint ?? "计算中…"}
                </dd>
              </div>
            </dl>
          ) : null}
        </section>
      </div>
    </AppShell>
  );
}