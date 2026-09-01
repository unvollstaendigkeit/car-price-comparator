import { cn, fmtPct, valuationTone } from "@/lib/format"
import { useT } from "@/lib/i18n/use-t"
import type { ConfidenceFlag } from "@/lib/types"

const CONFIDENCE_STYLES: Record<ConfidenceFlag, { cls: string; dot: string }> = {
  HIGH: { cls: "border-positive/40 bg-positive-soft/40 text-positive", dot: "bg-positive" },
  MEDIUM: { cls: "border-accent/40 bg-accent/10 text-accent", dot: "bg-accent" },
  LOW: { cls: "border-caution/40 bg-caution/10 text-caution", dot: "bg-caution" },
  INSUFFICIENT: { cls: "border-border-strong bg-surface-2 text-faint", dot: "bg-faint" },
}

export function ConfidenceBadge({ flag, className }: { flag: ConfidenceFlag; className?: string }) {
  const t = useT()
  const s = CONFIDENCE_STYLES[flag] ?? CONFIDENCE_STYLES.INSUFFICIENT
  const label = {
    HIGH: t.confidence.high,
    MEDIUM: t.confidence.medium,
    LOW: t.confidence.low,
    INSUFFICIENT: t.confidence.insufficient,
  }[flag] ?? t.confidence.insufficient
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium tracking-wide",
        s.cls,
        className,
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", s.dot)} aria-hidden />
      {label}
    </span>
  )
}

/**
 * Compact price-difference badge driven by undervaluation_pct.
 * positive => below market (good), negative => above market.
 */
export function DiffBadge({ pct }: { pct: number | null | undefined }) {
  const t = useT()
  if (pct === null || pct === undefined || Number.isNaN(pct)) return null
  const tone = valuationTone(pct)
  const cls =
    tone === "positive"
      ? "border-positive/40 bg-positive-soft/40 text-positive"
      : tone === "negative"
        ? "border-negative/40 bg-negative-soft/40 text-negative"
        : "border-border-strong bg-surface-2 text-muted"
  const label = tone === "positive" ? t.diff.belowMarket : tone === "negative" ? t.diff.aboveMarket : t.diff.atMarket
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[11px] tabular-nums",
        cls,
      )}
      title={`${label} (${t.diff.fromAsking})`}
    >
      {fmtPct(pct)}
    </span>
  )
}

/** Generic small pill for tier / sample-size annotations. */
export function MetaPill({
  children,
  tone = "neutral",
  title,
}: {
  children: React.ReactNode
  tone?: "neutral" | "accent" | "caution" | "danger"
  title?: string
}) {
  const tones = {
    neutral: "border-border-strong bg-surface-2 text-muted",
    accent: "border-accent/40 bg-accent/10 text-accent",
    caution: "border-caution/40 bg-caution/10 text-caution",
    danger: "border-danger/40 bg-danger-soft/40 text-danger",
  }
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[11px] uppercase tracking-wider",
        tones[tone],
      )}
    >
      {children}
    </span>
  )
}
