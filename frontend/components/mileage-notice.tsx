"use client"

import type { MileageMatch } from "@/lib/types"
import { cn, fmtKm } from "@/lib/format"

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
  /** Backend-authored sentence; used as an accessible/secondary description. */
  note?: string
  className?: string
}

const HEADLINE: Record<Exclude<MileageMatch, "good">, string> = {
  very_large: "Mileage mismatch",
  large: "Mileage differs materially",
  moderate: "Mileage differs somewhat",
  unknown: "Mileage not verified",
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
  note,
  className,
}: MileageNoticeProps) {
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
        Mileage closely matches comparable listings
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

  const dirWord = direction === "lower" ? "lower" : direction === "higher" ? "higher" : "different"

  // Context line: comparable range vs this car, in plain language.
  const rangeKnown = compP25 !== null && compP25 !== undefined && compP75 !== null && compP75 !== undefined
  const context =
    isUnknown
      ? note || "Comparable mileage was unavailable, so mileage similarity could not be checked."
      : `Comparable listings: ${rangeKnown ? `${fmtKmShort(compP25)}–${fmtKmShort(compP75)}` : fmtKmShort(compMedian)}` +
        `${submittedKm !== null && submittedKm !== undefined ? ` · This car: ${fmtKmShort(submittedKm)}` : ""}`

  const lead = isUnknown
    ? ""
    : `Most comparable cars have ${match === "very_large" ? "significantly " : ""}${dirWord} mileage than this vehicle` +
      `${compMedian !== null && compMedian !== undefined && submittedKm !== null && submittedKm !== undefined ? ` (median ${fmtKm(compMedian)} vs. ${fmtKm(submittedKm)}).` : "."}`

  return (
    <div
      className={cn("flex flex-col gap-1 rounded-md border px-3 py-2.5 text-[13px]", tone, className)}
      role={serious ? "alert" : undefined}
    >
      <p className="flex items-center gap-1.5 font-semibold">
        <span aria-hidden>{isUnknown ? "•" : "⚠"}</span>
        {HEADLINE[match]}
      </p>
      {lead && <p className="leading-relaxed opacity-90">{lead}</p>}
      <p className="font-mono text-[12px] tabular-nums opacity-80">{context}</p>
    </div>
  )
}

/** Small inline pill for dense tables. */
export function MileageBadge({ match, className }: { match: MileageMatch; className?: string }) {
  if (match === "good") {
    return (
      <span className={cn("text-[12px] text-positive", className)} title="Mileage closely matches comparables">
        ✓ mileage
      </span>
    )
  }
  const label: Record<Exclude<MileageMatch, "good">, string> = {
    very_large: "⚠ mileage mismatch",
    large: "⚠ mileage differs",
    moderate: "mileage differs",
    unknown: "mileage n/a",
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
