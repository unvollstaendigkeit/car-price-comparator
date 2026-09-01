/**
 * Plain-language copy for the structured, code-only warnings the backend
 * emits (see SampleWarning / RetrievalIssue / ConfidenceWarning in
 * lib/types.ts). This is the ONLY place these codes get turned into text -
 * the backend never sends prose, so a warning can't accidentally surface
 * exception text, ratios, or "n=1"-style jargon to a user.
 */
import type { ConfidenceWarning, SampleWarning, SourceKey } from "./types"
import { SOURCE_META } from "./types"

export function sampleWarningText(w: SampleWarning): string {
  switch (w.code) {
    case "single_listing":
      return "Based on a single listing — treat this as a rough guide, not a firm number."
    case "implausible_ratio":
      return "The listings found didn't look like real matches for this car, so this estimate has been hidden."
    default:
      return "This estimate may be unreliable."
  }
}

export function retrievalFailureText(sourceKey: SourceKey): string {
  const name = SOURCE_META[sourceKey].label
  return `${name} didn't respond in time, so it couldn't be checked for this car.`
}

export function confidenceWarningText(w: ConfidenceWarning): string {
  if (w.code === "source_disagreement") {
    return `${SOURCE_META.autobazar.label} and ${SOURCE_META.bazos.label} show quite different prices for this car (about ${Math.round(w.spread_pct)}% apart) — worth checking both before trusting either number.`
  }
  const name = SOURCE_META[w.source].label
  if (w.code === "retrieval_failed") {
    return w.reason === "timeout"
      ? `${name} didn't respond in time, so it couldn't be checked for this car.`
      : `${name} couldn't be reached, so it couldn't be checked for this car.`
  }
  return `${name}: ${sampleWarningText(w)}`
}
