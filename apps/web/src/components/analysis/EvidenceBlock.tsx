/**
 * 支持 / 反对证据（PRD 第 35.6 节）。
 *
 * **区分显示，不得只显示支持证据**。
 */

export interface EvidenceItem {
  text: string;
  /** 证据来源（指标、事件等）。 */
  source?: string;
  weight?: number | null;
}

export interface EvidenceBlockProps {
  supporting: EvidenceItem[];
  opposing: EvidenceItem[];
  /** 「未找到反对证据」时的说明文案。 */
  noOpposingHint?: string;
  className?: string;
}

export function EvidenceBlock(props: EvidenceBlockProps) {
  const { supporting, opposing, noOpposingHint, className } = props;

  return (
    <div className={`grid gap-4 md:grid-cols-2 ${className ?? ""}`} data-testid="evidence-block">
      <EvidenceColumn
        title="支持证据"
        tone="up"
        items={supporting}
        emptyHint="本次分析未提供支持证据。"
      />
      <EvidenceColumn
        title="反对证据"
        tone="down"
        items={opposing}
        emptyHint={noOpposingHint ?? "本次分析未提供反对证据。"}
      />
    </div>
  );
}

interface EvidenceColumnProps {
  title: string;
  tone: "up" | "down";
  items: EvidenceItem[];
  emptyHint: string;
}

function EvidenceColumn(props: EvidenceColumnProps) {
  const { title, tone, items, emptyHint } = props;
  const toneClass =
    tone === "up"
      ? "border-emerald-200 bg-emerald-50/40 text-emerald-900"
      : "border-red-200 bg-red-50/40 text-red-900";

  return (
    <section className={`rounded border p-3 ${toneClass}`} data-testid={`evidence-column-${tone}`}>
      <h3 className="mb-2 text-sm font-semibold">
        {title} <span className="font-normal opacity-60">({items.length})</span>
      </h3>
      {items.length === 0 ? (
        <p className="text-xs opacity-70" data-testid="evidence-empty">
          {emptyHint}
        </p>
      ) : (
        <ul className="space-y-1.5">
          {items.map((item, index) => (
            <li key={index} className="text-sm" data-testid="evidence-item">
              <span>{item.text}</span>
              {item.source ? <span className="ml-2 text-xs opacity-60">{item.source}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}