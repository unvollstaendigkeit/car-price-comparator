"use client"

import type { MileageMatch } from "@/lib/types"
import { cn, fmtKm } from "@/lib/format"
import { useT } from "@/lib/i18n/use-t"

/** Compact "118k km" style for tight banners. Falls back to fmtKm for small values. */
function fmtKmShort(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—"
  if (v >= 1000) return `${Math.round(v / 1000).toLocaleString("en-US")}k km`
  return fmtKm(v)
}

export interface MileageNoticeProps {
  match: MileageMatch
  /** Comparable mileage distribution (this source only — never merged). */
  compMedian?: number | null
  compP25?: number | null
  compP75?: number | null
  /** The submitted car's mileage. */
  submittedKm?: number | null
  /** 'lower' | 'higher' — comparables vs the submitted car. */
  direction?: "lower" | "higher" | "same" | "unknown"
  /** Backend-authored English sentence - kept for API compat, never rendered (see lib/warning-copy.ts's rationale: backend prose never crosses into UI copy; this component composes its own localized text from the structured fields instead). */
  note?: string
  className?: string
}

/**
 * Prominent, plain-language mileage-similarity signal for a single source.
 *
 * It surfaces the mileage-match confidence factor so a user notices it far more
 * easily than a technical confidence-reason string. It reflects — never
 * recomputes — the backend's assessment, and is rendered per source so
 * Autobazar and Bazoš evidence stay separate.
 */
export function MileageNotice({
  match,
  compMedian,
  compP25,
  compP75,
  submittedKm,
  direction,
  className,
}: MileageNoticeProps) {
  const t = useT()
  // Good match: a quiet, reassuring confirmation — no alarm needed.
  if (match === "good") {
    return (
      <p
        className={cn(
          "flex items-center gap-1.5 text-[13px] text-positive",
          className,
        )}
      >
        <span aria-hidden>✓</span>
        {t.mileage.goodMatch}
      </p>
    )
  }

  const serious = match === "very_large" || match === "large"
  const isUnknown = match === "unknown"

  const tone = serious
    ? "border-danger/30 bg-danger/10 text-danger"
    : isUnknown
      ? "border-border bg-surface text-muted"
      : "border-caution/30 bg-caution/10 text-caution"

  const dirWord = direction === "lower" ? t.mileage.dirLower : direction === "higher" ? t.mileage.dirHigher : t.mileage.dirDifferent

  // Context line: comparable range vs this car, in plain language.
  const rangeKnown = compP25 !== null && compP25 !== undefined && compP75 !== null && compP75 !== undefined
  const context =
    isUnknown
      ? t.mileage.unknownFallback
      : `${t.mileage.comparableListings} ${rangeKnown ? `${fmtKmShort(compP25)}–${fmtKmShort(compP75)}` : fmtKmShort(compMedian)}` +
        `${submittedKm !== null && submittedKm !== undefined ? ` · ${t.mileage.thisCar} ${fmtKmShort(submittedKm)}` : ""}`

  const hasMedianVsSubmitted =
    compMedian !== null && compMedian !== undefined && submittedKm !== null && submittedKm !== undefined
  const lead = isUnknown
    ? ""
    : t.mileage.lead(
        dirWord,
        match === "very_large",
        hasMedianVsSubmitted ? fmtKm(compMedian) : null,
        hasMedianVsSubmitted ? fmtKm(submittedKm) : null,
      )

  return (
    <div
      className={cn("flex flex-col gap-1 rounded-md border px-3 py-2.5 text-[13px]", tone, className)}
      role={serious ? "alert" : undefined}
    >
      <p className="flex items-center gap-1.5 font-semibold">
        <span aria-hidden>{isUnknown ? "•" : "⚠"}</span>
        {t.mileage.headline[match]}
      </p>
      {lead && <p className="leading-relaxed opacity-90">{lead}</p>}
      <p className="font-mono text-[12px] tabular-nums opacity-80">{context}</p>
    </div>
  )
}

/** Small inline pill for dense tables. */
export function MileageBadge({ match, className }: { match: MileageMatch; className?: string }) {
  const t = useT()
  if (match === "good") {
    return (
      <span className={cn("text-[12px] text-positive", className)} title={t.mileage.badgeGoodTitle}>
        {t.mileage.badgeGood}
      </span>
    )
  }
  const label: Record<Exclude<MileageMatch, "good">, string> = {
    very_large: t.mileage.badgeVeryLarge,
    large: t.mileage.badgeLarge,
    moderate: t.mileage.badgeModerate,
    unknown: t.mileage.badgeUnknown,
  }
  const serious = match === "very_large" || match === "large"
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-[12px] font-medium",
        serious ? "bg-danger/10 text-danger" : match === "unknown" ? "text-faint" : "bg-caution/10 text-caution",
        className,
      )}
    >
      {label[match]}
    </span>
  )
}
