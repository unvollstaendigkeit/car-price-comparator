/**
 * Plain-language stand-in for the backend's raw confidence_reasons /
 * confidence.reasons string ("tier reached: strict (best sample n=1)" style
 * jargon): which match tier this source's estimate came from, and how many
 * comparable listings backed it. Shared across single-car-result.tsx (the
 * results page), inventory-results-table.tsx (the Multi-car results page),
 * and exports.ts (both downloadable reports) so all of them show the SAME
 * info in the SAME words - never confidence_reasons itself, which is
 * backend prose that has no translation and was leaking straight through.
 *
 * Typed structurally (not SourceResult) so it also accepts
 * AnalysisSourceResult (the Multi-car/inventory per-source shape) - both
 * carry the same tier/comparable_count fields, nothing else here is needed.
 */
import type { Dictionary } from "./i18n/dictionaries"
import { tierLabel } from "./format"

export function tierSentence(
  t: Dictionary,
  label: string,
  s: { tier: string | null; comparable_count: number },
): string | null {
  if (!s.tier || s.comparable_count === 0) return null
  const tier = s.tier.toLowerCase()
  const found = tier === "strict" ? t.result.strictMatchesFound : t.result.onlyTierMatchesFound(tierLabel(s.tier, t.tier))
  return `${label}: ${found} — ${t.count.comparables(s.comparable_count)}.`
}
