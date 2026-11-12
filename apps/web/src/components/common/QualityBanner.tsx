/**
 * 数据质量横幅（PRD 第 35.5 节）。
 *
 * 数据质量必须可见 —— 这是本项目区别于普通行情网站的关键。
 *
 * | 数据质量 | 前端表现 |
 * |---|---|
 * | GOOD | 正常显示，不加标记 |
 * | DEGRADED | 黄色提示条：「部分数据降级，指标可能不完整」 |
 * | STALE | 橙色提示条 + 「最后更新于 X 前」+ 隐藏实时价格 |
 * | MISSING | 该区块显示占位符 + 「数据缺失」，**不显示 0 或上一次的值** |
 * | INVALID | 红色提示条 + 「数据异常，已排除」 |
 *
 * 禁止：把 MISSING 渲染成 0、把 STALE 的价格渲染成当前显示价、
 * 把缺失字段渲染成空白后让用户误以为是「没有异动」。
 */

import type { DataQuality } from "@/api/types";

export interface QualityBannerProps {
  quality: DataQuality;
  /** 最后更新时间（ISO-8601），用于 STALE 状态。 */
  lastUpdated?: string | null;
  /** MISSING 时是否隐藏实时价格。 */
  hideRealtime?: boolean;
  className?: string;
}

const QUALITY_STYLES: Record<DataQuality, { bg: string; text: string; message: string }> = {
  GOOD: { bg: "", text: "", message: "" },
  DEGRADED: {
    bg: "bg-yellow-50 border-yellow-300",
    text: "text-yellow-800",
    message: "部分数据降级，指标可能不完整",
  },
  STALE: {
    bg: "bg-orange-50 border-orange-300",
    text: "text-orange-800",
    message: "数据陈旧",
  },
  MISSING: {
    bg: "bg-slate-50 border-slate-300",
    text: "text-slate-700",
    message: "数据缺失",
  },
  INVALID: {
    bg: "bg-red-50 border-red-300",
    text: "text-red-800",
    message: "数据异常，已排除",
  },
};

export function QualityBanner(props: QualityBannerProps) {
  const { quality, lastUpdated, hideRealtime = false, className } = props;

  if (quality === "GOOD") {
    return null;
  }

  const style = QUALITY_STYLES[quality];
  const isStale = quality === "STALE";
  const isMissing = quality === "MISSING";

  return (
    <div
      role={quality === "INVALID" ? "alert" : "status"}
      data-testid={`quality-banner-${quality.toLowerCase()}`}
      data-quality={quality}
      className={`mb-3 rounded border px-3 py-2 text-sm ${style.bg} ${style.text} ${className ?? ""}`}
    >
      <span className="font-medium">{style.message}</span>
      {isStale && lastUpdated ? (
        <span className="ml-2">最后更新于 {formatRelative(lastUpdated)} 前</span>
      ) : null}
      {isMissing ? (
        <span className="ml-2">该区块无可用数据，未显示数值以避免误导。</span>
      ) : null}
      {isStale && hideRealtime ? (
        <span className="ml-2">实时价格已隐藏。</span>
      ) : null}
    </div>
  );
}

/** 相对时间格式化。 */
export function formatRelative(iso: string, now: Date = new Date()): string {
  const timestamp = Date.parse(iso);
  if (Number.isNaN(timestamp)) {
    return "未知时间";
  }
  const seconds = Math.max(0, Math.floor((now.getTime() - timestamp) / 1000));
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes} 分钟`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours} 小时`;
  }
  return `${Math.floor(hours / 24)} 天`;
}