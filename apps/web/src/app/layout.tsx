import type { Metadata, Viewport } from "next";

import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Crypto Market Intelligence",
  description: "加密市场多周期分析、AI 研判与评估追踪平台",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout(props: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <Providers>{props.children}</Providers>
      </body>
    </html>
  );
}