"use client"

import { useRef, useState } from "react"
import type { InventoryReport } from "@/lib/types"
import { loadInventoryDemo, parseInventory } from "@/lib/api"
import { cn } from "@/lib/format"
import { Disclosure } from "@/components/disclosure"
import { InventoryReviewTable } from "@/components/inventory-review-table"

type Phase = "upload" | "review" | "ready"

const ACCEPT = ".csv,.xlsx,.xls"

/**
 * Inventory workflow: upload → normalize → validate → review → confirm.
 *
 * This phase performs NO marketplace scraping. Reuses the shared backend
 * normalizer (same as single-car) via /api/inventory/*. After the user
 * confirms, it stops at a "ready to analyze" placeholder — the batch market
 * analysis is a later phase.
 */
export function InventoryModule() {
  const [phase, setPhase] = useState<Phase>("upload")
  const [report, setReport] = useState<InventoryReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)

  async function ingest(run: () => Promise<InventoryReport>) {
    setLoading(true)
    setError(null)
    try {
      const rep = await run()
      setReport(rep)
      setPhase("review")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not read that file.")
    } finally {
      setLoading(false)
    }
  }

  const onFile = (file: File | undefined) => {
    if (!file) return
    void ingest(() => parseInventory(file))
  }

  const reset = () => {
    setReport(null)
    setError(null)
    setPhase("upload")
    if (inputRef.current) inputRef.current.value = ""
  }

  if (phase === "ready" && report) {
    return <ReadyPanel report={report} onBack={() => setPhase("review")} onReset={reset} />
  }

  if (phase === "review" && report) {
    return (
      <ReviewPanel
        report={report}
        onConfirm={() => setPhase("ready")}
        onReset={reset}
      />
    )
  }

  return (
    <UploadPanel
      loading={loading}
      error={error}
      dragging={dragging}
      inputRef={inputRef}
      accept={ACCEPT}
      onPick={() => inputRef.current?.click()}
      onFile={onFile}
      onDemo={(name) => void ingest(() => loadInventoryDemo(name))}
      setDragging={setDragging}
    />
  )
}

/* --------------------------------------------------------------------------- */
/* Upload                                                                      */
/* --------------------------------------------------------------------------- */
function UploadPanel({
  loading,
  error,
  dragging,
  inputRef,
  accept,
  onPick,
  onFile,
  onDemo,
  setDragging,
}: {
  loading: boolean
  error: string | null
  dragging: boolean
  inputRef: React.RefObject<HTMLInputElement | null>
  accept: string
  onPick: () => void
  onFile: (f: File | undefined) => void
  onDemo: (name: "sample" | "alt") => void
  setDragging: (v: boolean) => void
}) {
  return (
    <div className="flex flex-col gap-5 rounded-lg border border-border bg-surface p-5 md:p-6">
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold text-foreground">Upload your inventory</h2>
        <p className="text-[15px] text-muted">
          Drop a dealer spreadsheet and we&apos;ll detect the columns automatically — any reasonable layout works. No
          fixed template required.
        </p>
      </div>

      <button
        type="button"
        onClick={onPick}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          onFile(e.dataTransfer.files?.[0])
        }}
        disabled={loading}
        className={cn(
          "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-6 py-12 text-center transition-colors",
          dragging ? "border-accent bg-accent/5" : "border-border-strong hover:border-accent/60 hover:bg-surface-2/40",
          loading && "pointer-events-none opacity-60",
        )}
      >
        <svg viewBox="0 0 24 24" className="h-7 w-7 text-faint" fill="none" aria-hidden="true">
          <path
            d="M12 16V4m0 0L8 8m4-4 4 4M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <span className="text-sm font-medium text-foreground">
          {loading ? "Reading spreadsheet…" : "Drop a file or click to browse"}
        </span>
        <span className="text-[13px] text-faint">Excel (.xlsx) or CSV (.csv)</span>
      </button>

      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="sr-only"
        onChange={(e) => onFile(e.target.files?.[0])}
      />

      {error && (
        <div className="rounded-lg border border-danger/40 bg-danger-soft/30 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border pt-4">
        <span className="text-[13px] text-faint">No file handy? Try a demo:</span>
        <button
          type="button"
          onClick={() => onDemo("sample")}
          disabled={loading}
          className="rounded-md border border-border px-2.5 py-1 text-[13px] text-muted hover:border-border-strong hover:text-foreground disabled:opacity-40"
        >
          98-car inventory
        </button>
        <button
          type="button"
          onClick={() => onDemo("alt")}
          disabled={loading}
          className="rounded-md border border-border px-2.5 py-1 text-[13px] text-muted hover:border-border-strong hover:text-foreground disabled:opacity-40"
        >
          Alternative format
        </button>
      </div>
    </div>
  )
}

/* --------------------------------------------------------------------------- */
/* Review                                                                      */
/* --------------------------------------------------------------------------- */
function ReviewPanel({
  report,
  onConfirm,
  onReset,
}: {
  report: InventoryReport
  onConfirm: () => void
  onReset: () => void
}) {
  const { counts } = report
  const ready = counts.valid_for_comparison

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-5 md:p-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            <p className="text-[13px] uppercase tracking-wide text-faint">{report.source_name}</p>
            <h2 className="text-2xl font-semibold text-foreground">
              {counts.total_rows} {counts.total_rows === 1 ? "car" : "cars"} detected
            </h2>
          </div>
          <button
            type="button"
            onClick={onReset}
            className="rounded-md border border-border px-3 py-1.5 text-[13px] font-medium text-muted hover:border-border-strong hover:text-foreground"
          >
            Upload a different file
          </button>
        </div>

        <div className="flex flex-wrap gap-2">
          <Stat label="Ready" value={counts.valid_for_comparison} tone="positive" />
          <Stat label="Needs review" value={counts.review} tone="caution" />
          <Stat label="Sold / unavailable" value={counts.sold_or_unavailable} tone="muted" />
        </div>

        {(report.mapping.length > 0 || report.ambiguities.length > 0 || report.unmapped_columns.length > 0) && (
          <Disclosure summary="How your columns were mapped">
            <div className="flex flex-col gap-3 text-[13px]">
              <div className="flex flex-col gap-1">
                {report.mapping.map((m) => (
                  <div key={m.field} className="flex items-center gap-2 text-muted">
                    <span className="font-mono text-foreground">{m.column}</span>
                    <span className="text-faint">→</span>
                    <span>{m.field}</span>
                  </div>
                ))}
              </div>
              {report.unmapped_columns.length > 0 && (
                <p className="text-faint">
                  Ignored columns: {report.unmapped_columns.join(", ")}
                </p>
              )}
              {report.ambiguities.length > 0 && (
                <div className="flex flex-col gap-1">
                  {report.ambiguities.map((a, i) => (
                    <p key={i} className="text-caution">
                      {a}
                    </p>
                  ))}
                </div>
              )}
            </div>
          </Disclosure>
        )}
      </div>

      <InventoryReviewTable report={report} />

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface px-5 py-4">
        <p className="text-[13px] text-faint">
          No marketplace searches have run yet. Continue when the detected cars look right.
        </p>
        <button
          type="button"
          onClick={onConfirm}
          disabled={ready === 0}
          className="rounded-md bg-accent px-5 py-2.5 text-sm font-semibold text-accent-foreground transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
        >
          Continue to valuation →
        </button>
      </div>
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone: "positive" | "caution" | "muted" }) {
  const toneCls =
    tone === "positive" ? "text-positive" : tone === "caution" ? "text-caution" : "text-muted"
  return (
    <div className="flex items-baseline gap-2 rounded-md border border-border bg-surface-2/50 px-3 py-2">
      <span className={cn("font-mono text-lg font-semibold tabular-nums", toneCls)}>{value}</span>
      <span className="text-[13px] text-faint">{label}</span>
    </div>
  )
}

/* --------------------------------------------------------------------------- */
/* Ready (placeholder — next phase does the actual market analysis)            */
/* --------------------------------------------------------------------------- */
function ReadyPanel({
  report,
  onBack,
  onReset,
}: {
  report: InventoryReport
  onBack: () => void
  onReset: () => void
}) {
  const ready = report.counts.valid_for_comparison
  return (
    <div className="flex flex-col items-center gap-5 rounded-lg border border-border bg-surface px-6 py-14 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-positive-soft/40 text-positive">
        <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" aria-hidden="true">
          <path d="m5 13 4 4L19 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <div className="flex flex-col gap-1.5">
        <h2 className="text-2xl font-semibold text-foreground">Ready to analyze {ready} cars</h2>
        <p className="mx-auto max-w-md text-[15px] leading-relaxed text-muted">
          Your inventory is normalized and validated. The next phase will value each car against Autobazar.eu and
          Bazoš.sk — the same two-source comparison used in Single car mode. No searches have run yet.
        </p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <button
          type="button"
          onClick={onBack}
          className="rounded-md border border-border px-4 py-2.5 text-sm font-medium text-muted hover:border-border-strong hover:text-foreground"
        >
          Back to review
        </button>
        <button
          type="button"
          onClick={onReset}
          className="rounded-md border border-border px-4 py-2.5 text-sm font-medium text-muted hover:border-border-strong hover:text-foreground"
        >
          Start over
        </button>
        <button
          type="button"
          disabled
          title="Coming in the next phase"
          className="rounded-md bg-accent px-5 py-2.5 text-sm font-semibold text-accent-foreground opacity-40"
        >
          Run market analysis (coming soon)
        </button>
      </div>
    </div>
  )
}
