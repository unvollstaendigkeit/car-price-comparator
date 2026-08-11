import { cn } from "@/lib/format"
import type { ConfidenceFlag } from "@/lib/types"

const CONFIDENCE_STYLES: Record<ConfidenceFlag, { label: string; cls: string; dot: string }> = {
  HIGH: {
    label: "High confidence",
    cls: "border-pos/40 bg-pos/10 text-pos",
    dot: "bg-pos",
  },
  MEDIUM: {
    label: "Medium confidence",
    cls: "border-accent/40 bg-accent/10 text-accent",
    dot: "bg-accent",
  },
  LOW: {
    label: "Low confidence",
    cls: "border-caution/40 bg-caution/10 text-caution",
    dot: "bg-caution",
  },
  INSUFFICIENT: {
    label: "Insufficient data",
    cls: "border-muted-foreground/30 bg-muted/40 text-muted-foreground",
    dot: "bg-muted-foreground",
  },
}

export function ConfidenceBadge({ flag, className }: { flag: ConfidenceFlag; className?: string }) {
  const s = CONFIDENCE_STYLES[flag] ?? CONFIDENCE_STYLES.INSUFFICIENT
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium tracking-wide",
        s.cls,
        className,
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", s.dot)} aria-hidden />
      {s.label}
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
    neutral: "border-border bg-muted/40 text-muted-foreground",
    accent: "border-accent/40 bg-accent/10 text-accent",
    caution: "border-caution/40 bg-caution/10 text-caution",
    danger: "border-neg/40 bg-neg/10 text-neg",
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
