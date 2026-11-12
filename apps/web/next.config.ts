import type { NextConfig } from "next";

const API_TARGET = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // 35.11：不硬编码后端 URL，同源代理解决
  async rewrites() {
    return [
      // 业务端点：/api/v1/** → 后端
      {
        source: "/api/:path*",
        destination: `${API_TARGET}/api/:path*`,
      },
      // 探针端点位于后端根路径（31.7），不带 /api 前缀
      {
        source: "/health",
        destination: `${API_TARGET}/health`,
      },
      {
        source: "/ready",
        destination: `${API_TARGET}/ready`,
      },
    ];
  },
  eslint: {
    // lint 由独立的 `npm run lint` 执行，构建不阻塞
    ignoreDuringBuilds: true,
  },
  typescript: {
    ignoreBuildErrors: false,
  },
};

export default nextConfig;