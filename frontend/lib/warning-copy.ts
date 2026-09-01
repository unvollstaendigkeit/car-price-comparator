/**
 * Plain-language copy for the structured, code-only warnings the backend
 * emits (see SampleWarning / RetrievalIssue / ConfidenceWarning in
 * lib/types.ts). This is the ONLY place these codes get turned into text -
 * the backend never sends prose, so a warning can't accidentally surface
 * exception text, ratios, or "n=1"-style jargon to a user.
 *
 * All wording itself lives in the `t` dictionary passed in (lib/i18n) - this
 * module just resolves a code (+ source name) to the right dictionary entry.
 */
import type { Dictionary } from "./i18n/dictionaries"
import type { ConfidenceWarning, SampleWarning, SourceKey } from "./types"
import { SOURCE_META } from "./types"

export function sampleWarningText(t: Dictionary, w: SampleWarning): string {
  switch (w.code) {
    case "single_listing":
      return t.warnings.singleListing
    case "implausible_ratio":
      return t.warnings.implausibleRatio
    default:
      return t.warnings.unreliable
  }
}

export function retrievalFailureText(t: Dictionary, sourceKey: SourceKey): string {
  return t.warnings.retrievalTimeout(SOURCE_META[sourceKey].label)
}

export function confidenceWarningText(t: Dictionary, w: ConfidenceWarning): string {
  if (w.code === "source_disagreement") {
    return t.warnings.sourceDisagreement(SOURCE_META.autobazar.label, SOURCE_META.bazos.label, Math.round(w.spread_pct))
  }
  const name = SOURCE_META[w.source].label
  if (w.code === "retrieval_failed") {
    return w.reason === "timeout" ? t.warnings.retrievalTimeout(name) : t.warnings.retrievalBlocked(name)
  }
  return `${name}: ${sampleWarningText(t, w)}`
}
