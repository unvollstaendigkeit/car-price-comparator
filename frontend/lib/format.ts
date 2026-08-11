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

export function tierLabel(tier: string | null | undefined): string {
  if (!tier) return "—"
  const map: Record<string, string> = {
    strict: "Strict match",
    STRICT: "Strict match",
    relaxed: "Relaxed",
    RELAXED: "Relaxed",
    broad: "Broad",
    BROAD: "Broad",
  }
  return map[tier] ?? tier.charAt(0).toUpperCase() + tier.slice(1).toLowerCase()
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
