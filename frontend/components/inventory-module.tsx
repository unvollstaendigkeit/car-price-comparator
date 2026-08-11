"use client"

import { useEffect, useRef, useState } from "react"
import type { AnalysisCarResult, AnalysisEvent, AnalysisSummary, InventoryReport, InventoryRow } from "@/lib/types"
import { loadInventoryDemo, parseInventory, streamInventoryAnalysis } from "@/lib/api"
import { cn } from "@/lib/format"
import { Disclosure } from "@/components/disclosure"
import { InventoryReviewTable } from "@/components/inventory-review-table"
import { InventoryResultsTable } from "@/components/inventory-results-table"

type Phase = "upload" | "review" | "ready" | "analyzing"

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
  // Persistent market cache controls: `refresh` forces a live re-fetch of every
  // model; `runId` remounts AnalysisPanel to start a fresh run (e.g. on refresh).
  const [refresh, setRefresh] = useState(false)
  const [runId, setRunId] = useState(0)
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
    setRefresh(false)
    if (inputRef.current) inputRef.current.value = ""
  }

  const startRun = (forceRefresh: boolean) => {
    setRefresh(forceRefresh)
    setRunId((n) => n + 1)
    setPhase("analyzing")
  }

  if (phase === "analyzing" && report) {
    return (
      <AnalysisPanel
        key={runId}
        rows={report.rows.filter((r) => r.valid_for_comparison)}
        refresh={refresh}
        onBack={() => setPhase("ready")}
        onReset={reset}
        onRefreshRun={() => startRun(true)}
      />
    )
  }

  if (phase === "ready" && report) {
    return (
      <ReadyPanel
        report={report}
        refresh={refresh}
        onRefreshChange={setRefresh}
        onBack={() => setPhase("review")}
        onReset={reset}
        onRun={() => startRun(refresh)}
      />
    )
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
  refresh,
  onRefreshChange,
  onBack,
  onReset,
  onRun,
}: {
  report: InventoryReport
  refresh: boolean
  onRefreshChange: (v: boolean) => void
  onBack: () => void
  onReset: () => void
  onRun: () => void
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
          Each car is valued against Autobazar.eu and Bazoš.sk — the same two-source comparison used in Single car
          mode. Cars are grouped by make &amp; model, so shared searches keep requests low and the two sources stay
          separate (never blended). Market data for each model is cached for 24h, so repeat runs reuse it with no new
          requests.
        </p>
      </div>
      <label className="flex cursor-pointer items-center gap-2.5 rounded-md border border-border bg-surface-2/50 px-3.5 py-2 text-[13px] text-muted">
        <input
          type="checkbox"
          checked={refresh}
          onChange={(e) => onRefreshChange(e.target.checked)}
          className="h-4 w-4 accent-accent"
        />
        <span>
          Refresh market data <span className="text-faint">— ignore the cache and fetch every model live</span>
        </span>
      </label>
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
          onClick={onRun}
          disabled={ready === 0}
          className="rounded-md bg-accent px-5 py-2.5 text-sm font-semibold text-accent-foreground transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
        >
          Run market analysis →
        </button>
      </div>
    </div>
  )
}

/* --------------------------------------------------------------------------- */
/* Analysis (live SSE stream: retrieval grouped per make+model)                */
/* --------------------------------------------------------------------------- */
interface Progress {
  analyzed: number
  total: number
  groupIndex: number
  groupTotal: number
  currentModel: string
  cached: boolean
  lookups: number
}

interface CacheLogEntry {
  model: string
  cached: boolean
  ageS: number | null
}

/** Human-readable age of a cached pool, e.g. "just now", "3h old", "2d old". */
function formatAge(ageS: number | null): string {
  if (ageS == null) return ""
  if (ageS < 90) return "just now"
  const mins = Math.round(ageS / 60)
  if (mins < 90) return `${mins}m old`
  const hours = Math.round(ageS / 3600)
  if (hours < 36) return `${hours}h old`
  return `${Math.round(ageS / 86400)}d old`
}

function AnalysisPanel({
  rows,
  refresh,
  onBack,
  onReset,
  onRefreshRun,
}: {
  rows: InventoryRow[]
  refresh: boolean
  onBack: () => void
  onReset: () => void
  onRefreshRun: () => void
}) {
  const [status, setStatus] = useState<"running" | "done" | "error">("running")
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [progress, setProgress] = useState<Progress>({
    analyzed: 0,
    total: rows.length,
    groupIndex: 0,
    groupTotal: 0,
    currentModel: "",
    cached: false,
    lookups: 0,
  })
  const [results, setResults] = useState<AnalysisCarResult[]>([])
  const [summary, setSummary] = useState<AnalysisSummary | null>(null)
  const [notices, setNotices] = useState<string[]>([])
  const [cacheLog, setCacheLog] = useState<CacheLogEntry[]>([])

  useEffect(() => {
    const controller = new AbortController()
    let lookups = 0

    streamInventoryAnalysis(
      rows,
      (e: AnalysisEvent) => {
        switch (e.stage) {
          case "start":
            setProgress((p) => ({ ...p, total: e.total_cars, groupTotal: e.total_groups }))
            break
          case "group_start": {
            if (!e.cached) lookups += 2
            // Oldest of the two sources' ages best represents the group's freshness.
            const ages = [e.cache_status.autobazar.age_s, e.cache_status.bazos.age_s].filter(
              (a): a is number => a != null,
            )
            const ageS = e.cached && ages.length ? Math.max(...ages) : null
            setProgress((p) => ({
              ...p,
              groupIndex: e.group_index,
              groupTotal: e.group_total,
              currentModel: `${e.brand} ${e.model}`,
              cached: e.cached,
              lookups,
            }))
            setCacheLog((log) => [...log, { model: `${e.brand} ${e.model}`, cached: e.cached, ageS }])
            break
          }
          case "source_disabled":
            setNotices((n) => [...n, `${e.source === "autobazar" ? "Autobazar.eu" : "Bazoš.sk"}: ${e.reason}`])
            break
          case "car_done":
            setResults((r) => [...r, e.car])
            setProgress((p) => ({ ...p, analyzed: e.analyzed, total: e.total }))
            break
          case "summary":
            setSummary({
              counts: e.counts,
              market_lookups: e.market_lookups,
              disabled_sources: e.disabled_sources,
              ranked: e.ranked,
              benchmark: e.benchmark,
            })
            setStatus("done")
            break
          case "error":
            setErrorMsg(e.message)
            setStatus("error")
            break
        }
      },
      controller.signal,
      { refresh },
    ).catch((err) => {
      if (controller.signal.aborted) return
      setErrorMsg(err instanceof Error ? err.message : "Analysis failed.")
      setStatus("error")
    })

    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Ranked view: final order from the summary, or a live client-side sort while running.
  const ranked =
    summary?.ranked ??
    [...results]
      .filter((r) => r.rank_price_diff_pct !== null)
      .sort((a, b) => (b.rank_price_diff_pct ?? 0) - (a.rank_price_diff_pct ?? 0))

  const pct = progress.total > 0 ? Math.round((progress.analyzed / progress.total) * 100) : 0
  const cachedModels = cacheLog.filter((c) => c.cached).length
  const liveModels = cacheLog.filter((c) => !c.cached).length

  return (
    <div className="flex flex-col gap-5">
      {/* Progress / summary header */}
      <div className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-5 md:p-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-col gap-1">
            <p className="text-[13px] uppercase tracking-wide text-faint">
              {status === "running" ? "Analyzing inventory" : status === "done" ? "Analysis complete" : "Analysis stopped"}
            </p>
            <h2 className="text-2xl font-semibold text-foreground">
              {progress.analyzed} / {progress.total} cars valued
            </h2>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {status !== "running" && (
              <>
                <button
                  type="button"
                  onClick={onRefreshRun}
                  title="Ignore cached market data and re-fetch every model live"
                  className="flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-[13px] font-medium text-muted hover:border-border-strong hover:text-foreground"
                >
                  <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" aria-hidden="true">
                    <path
                      d="M21 12a9 9 0 1 1-2.64-6.36M21 4v4h-4"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                  Refresh market data
                </button>
                <button
                  type="button"
                  onClick={onBack}
                  className="rounded-md border border-border px-3 py-1.5 text-[13px] font-medium text-muted hover:border-border-strong hover:text-foreground"
                >
                  Back
                </button>
              </>
            )}
            <button
              type="button"
              onClick={onReset}
              className="rounded-md border border-border px-3 py-1.5 text-[13px] font-medium text-muted hover:border-border-strong hover:text-foreground"
            >
              Start over
            </button>
          </div>
        </div>

        <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
          <div
            className={cn("h-full rounded-full transition-all", status === "error" ? "bg-danger" : "bg-accent")}
            style={{ width: `${status === "done" ? 100 : pct}%` }}
          />
        </div>

        {status === "running" && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted">
            <span className="flex items-center gap-2">
              <span className="block h-3 w-3 animate-spin rounded-full border border-accent border-t-transparent" aria-hidden />
              {progress.cached ? "Reusing cached market for" : "Searching"}{" "}
              <span className="text-foreground">{progress.currentModel || "…"}</span>
            </span>
            <span className="text-faint">
              model {progress.groupIndex}/{progress.groupTotal}
            </span>
            <span className="text-faint">
              {cachedModels} cached · {liveModels} live · {progress.lookups} live lookups
            </span>
          </div>
        )}

        {summary && (
          <div className="flex flex-wrap gap-2">
            <SummaryStat label="High" value={summary.counts.high} tone="positive" />
            <SummaryStat label="Medium" value={summary.counts.medium} tone="accent" />
            <SummaryStat label="Low" value={summary.counts.low} tone="caution" />
            <SummaryStat label="Insufficient" value={summary.counts.insufficient} tone="muted" />
            <SummaryStat label="Live requests" value={summary.benchmark?.http_requests ?? summary.market_lookups} tone="muted" />
            {summary.benchmark && summary.benchmark.cached_groups > 0 && (
              <SummaryStat label="Models from cache" value={summary.benchmark.cached_groups} tone="positive" />
            )}
          </div>
        )}

        {/* Per-model cache/live activity log — proves reuse and shows data age. */}
        {cacheLog.length > 0 && (
          <div className="rounded-md border border-border bg-surface-2/40 p-3">
            <p className="mb-2 text-[12px] uppercase tracking-wide text-faint">Market data source per model</p>
            <ul className="flex max-h-44 flex-col gap-1 overflow-y-auto text-[13px]">
              {cacheLog.map((c, i) => (
                <li key={`${c.model}-${i}`} className="flex items-center justify-between gap-3">
                  <span className="truncate text-foreground">{c.model}</span>
                  {c.cached ? (
                    <span className="flex shrink-0 items-center gap-1.5 text-positive">
                      <span className="block h-1.5 w-1.5 rounded-full bg-positive" aria-hidden />
                      cached{c.ageS != null ? ` · ${formatAge(c.ageS)}` : ""}
                    </span>
                  ) : (
                    <span className="flex shrink-0 items-center gap-1.5 text-muted">
                      <span className="block h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
                      fetched live
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {notices.map((n, i) => (
          <p key={i} className="rounded-md border border-caution/40 bg-caution/10 px-3 py-2 text-[13px] text-caution">
            {n}
          </p>
        ))}

        {errorMsg && (
          <div className="rounded-lg border border-danger/40 bg-danger-soft/30 px-4 py-3 text-sm text-danger">
            {errorMsg}
          </div>
        )}
      </div>

      {ranked.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-[13px] text-faint">Ranked most below market → least. Click a row for source details.</p>
          <InventoryResultsTable cars={ranked} />
        </div>
      )}
    </div>
  )
}

function SummaryStat({
  label,
  value,
  tone,
}: {
  label: string
  value: number
  tone: "positive" | "accent" | "caution" | "muted"
}) {
  const toneCls =
    tone === "positive"
      ? "text-positive"
      : tone === "accent"
        ? "text-accent"
        : tone === "caution"
          ? "text-caution"
          : "text-muted"
  return (
    <div className="flex items-baseline gap-2 rounded-md border border-border bg-surface-2/50 px-3 py-2">
      <span className={cn("font-mono text-lg font-semibold tabular-nums", toneCls)}>{value}</span>
      <span className="text-[13px] text-faint">{label}</span>
    </div>
  )
}
