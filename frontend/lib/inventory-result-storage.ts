/**
 * Hands a finished Multi-car analysis off to a new tab (its own URL:
 * /inventory-result?id=...) - the same "own tab, own URL" idea single-car
 * already has via /result?brand=...&model=..., except an inventory (dozens
 * to hundreds of rows) doesn't fit in a URL the way a handful of car fields
 * do, so this uses localStorage instead.
 *
 * NOT a shareable link: this only works in the SAME browser that ran the
 * analysis - opening the URL anywhere else (another device, another
 * person's browser, even a different browser on this machine) finds
 * nothing. The actual shareable artifact remains the export
 * (exportInventoryReport/exportInventoryXlsx in lib/exports.ts) - a real,
 * standalone file, no storage dependency at all. If genuine cross-device
 * sharing of a live results page is ever wanted, that needs real
 * server-side storage (this app has none today) - a deliberate later step,
 * not something this module tries to fake.
 *
 * localStorage over sessionStorage: sessionStorage's inheritance into a
 * window.open()'d tab is opener-relationship-dependent and inconsistent
 * across browsers/flags (e.g. noopener); localStorage is just per-origin,
 * so it works regardless of how the new tab was opened and survives a
 * reload of it. Only ONE result is kept at a time (each save overwrites the
 * last) to stay comfortably under localStorage's ~5-10MB per-origin quota -
 * a full inventory's comparable listings can be sizable.
 */
import type { AnalysisCarResult, AnalysisSummary } from "./types"

const STORAGE_KEY = "carval:inventory-result"

interface StoredResult {
  id: string
  ranked: AnalysisCarResult[]
  summary: AnalysisSummary | null
  savedAt: number
}

/** Saves the result and returns the id to put in the new tab's URL. */
export function saveInventoryResult(ranked: AnalysisCarResult[], summary: AnalysisSummary | null): string {
  const id = crypto.randomUUID()
  const payload: StoredResult = { id, ranked, summary, savedAt: Date.now() }
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
  } catch {
    // Storage full or disabled (e.g. private browsing) - the caller still
    // gets an id and can open the tab; it'll just show the "not found" state.
  }
  return id
}

/** Returns null if nothing is stored, or if a newer result has since overwritten this id. */
export function loadInventoryResult(
  id: string,
): { ranked: AnalysisCarResult[]; summary: AnalysisSummary | null } | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const stored = JSON.parse(raw) as StoredResult
    if (stored.id !== id) return null
    return { ranked: stored.ranked, summary: stored.summary }
  } catch {
    return null
  }
}
