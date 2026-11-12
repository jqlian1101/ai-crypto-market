"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

/**
 * TanStack Query Provider。
 *
 * 默认重取策略：聚焦时重取 + 窗口重取，与 PRD 35.3 的轮询策略配合。
 */
export function Providers(props: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 1_000,
            gcTime: 5 * 60_000,
            refetchOnWindowFocus: true,
            retry: 2,
          },
        },
      }),
  );

  return <QueryClientProvider client={client}>{props.children}</QueryClientProvider>;
}