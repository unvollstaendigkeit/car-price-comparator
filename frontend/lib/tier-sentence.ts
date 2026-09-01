/**
 * Plain-language stand-in for the backend's raw confidence.reasons string
 * ("tier reached: strict (best sample n=1)" style jargon): which match tier
 * this source's estimate came from, and how many comparable listings backed
 * it. Shared between single-car-result.tsx (the results page) and
 * exports.ts (the downloadable report) so both show the SAME info in the
 * SAME words - never confidence.reasons itself, which is backend prose that
 * has no translation and was leaking into the export.
 */
import type { Dictionary } from "./i18n/dictionaries"
import type { SourceResult } from "./types"
import { tierLabel } from "./format"

export function tierSentence(t: Dictionary, label: string, s: SourceResult): string | null {
  if (!s.tier || s.comparable_count === 0) return null
  const tier = s.tier.toLowerCase()
  const found = tier === "strict" ? t.result.strictMatchesFound : t.result.onlyTierMatchesFound(tierLabel(s.tier, t.tier))
  return `${label}: ${found} — ${t.count.comparables(s.comparable_count)}.`
}
