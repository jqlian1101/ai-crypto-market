"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { apiGet } from "@/api/client";

interface ShortLinkPayload {
  target_path: string;
  expires_at?: string | null;
}

/**
 * 短链跳转页（PRD 35.2）。
 *
 * URL 必须深链接可分享：推送中的「详情链接」都指向可独立访问的 URL，
 * 短链 `/s/{id}` → 具体页面。
 *
 * 短链解析在**客户端**完成，因为访问令牌存于 `sessionStorage`，
 * 服务端无法携带 `Authorization` 头。
 */
export default function ShortLinkPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const id = params.id;
    let cancelled = false;

    void (async () => {
      try {
        const result = await apiGet<ShortLinkPayload>(`/api/v1/shortlinks/${encodeURIComponent(id)}`);
        if (cancelled) {
          return;
        }
        const target = result.data?.target_path;
        if (target) {
          router.replace(target);
        } else {
          setError("短链未指向任何页面");
        }
      } catch {
        if (!cancelled) {
          setError("短链解析失败或已过期");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [params.id, router]);

  return (
    <main className="mx-auto max-w-md p-8 text-center">
      {error ? (
        <p className="text-sm text-red-700" data-testid="short-link-error">
          {error}
        </p>
      ) : (
        <p className="text-sm text-slate-500">正在跳转…</p>
      )}
    </main>
  );
}