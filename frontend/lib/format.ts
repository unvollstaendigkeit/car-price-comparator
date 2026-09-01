import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function fmtEur(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—"
  return `€${Math.round(v).toLocaleString("en-US")}`
}

export function fmtSignedEur(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—"
  const sign = v > 0 ? "+" : v < 0 ? "−" : ""
  return `${sign}€${Math.abs(Math.round(v)).toLocaleString("en-US")}`
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—"
  const sign = v > 0 ? "+" : v < 0 ? "−" : ""
  return `${sign}${Math.abs(v).toFixed(digits)}%`
}

/**
 * Magnitude-only percentage, no +/− sign. Used where accompanying words
 * ("below market" / "above market") already convey direction, so a sign would
 * be redundant and confusing.
 */
export function fmtPctPlain(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—"
  return `${Math.abs(v).toFixed(digits)}%`
}

export function fmtKm(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—"
  return `${Math.round(v).toLocaleString("en-US")} km`
}

export function fmtYear(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—"
  return String(Math.round(v))
}

// Default (English) tier labels, used where a caller hasn't been localized
// yet. Pass `t.tier` (see lib/i18n) from a localized caller instead.
const DEFAULT_TIER_LABELS: Record<string, string> = { strict: "Strict match", moderate: "Moderate", broad: "Broad" }

export function tierLabel(tier: string | null | undefined, labels: Record<string, string> = DEFAULT_TIER_LABELS): string {
  if (!tier) return "—"
  return labels[tier.toLowerCase()] ?? tier.charAt(0).toUpperCase() + tier.slice(1).toLowerCase()
}

/**
 * Localizes a canonical wire value (fuel/transmission/body type - always
 * English on the wire, e.g. "Manual", "Diesel", so backend matching never
 * breaks) into its display label. Passes null/undefined straight through
 * unchanged, so existing `|| "—"` / `.filter(Boolean)` call-site patterns
 * keep working exactly as before. An unmapped value falls back to itself
 * rather than disappearing.
 */
export function mapLabel<T extends string | null | undefined>(value: T, labels: Record<string, string>): T | string {
  if (!value) return value
  return labels[value] ?? value
}

/** Translates a raw comma-joined field-name list (e.g. "year,fuel") via `labels` (t.fieldNames). */
export function missingFieldsLabel(csv: string, labels: Record<string, string>): string {
  return csv
    .split(",")
    .map((f) => labels[f.trim()] ?? f.trim())
    .join(", ")
}

/**
 * undervaluation_pct sign convention (backend definition):
 *   positive -> cheaper than market median (a potential deal)
 *   negative -> more expensive than market median
 */
export function valuationTone(
  pctVal: number | null | undefined,
): "positive" | "negative" | "neutral" {
  if (pctVal === null || pctVal === undefined || Number.isNaN(pctVal)) return "neutral"
  if (pctVal >= 2) return "positive"
  if (pctVal <= -2) return "negative"
  return "neutral"
}
