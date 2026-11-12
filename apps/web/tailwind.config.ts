import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 35.8：颜色语义全局统一（涨=绿/跌=红），集中在此，组件里不散落
        market: {
          up: "#16a34a",
          down: "#dc2626",
          flat: "#64748b",
        },
        // 35.5 / 35.6：数据质量与状态色
        quality: {
          good: "#16a34a",
          degraded: "#eab308",
          stale: "#f97316",
          missing: "#94a3b8",
          invalid: "#dc2626",
        },
        severity: {
          info: "#0284c7",
          warn: "#d97706",
          critical: "#dc2626",
        },
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
