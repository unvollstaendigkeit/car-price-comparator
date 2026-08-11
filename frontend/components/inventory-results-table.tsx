"use client"

import { useState } from "react"
import type { AnalysisCarResult, AnalysisSourceResult } from "@/lib/types"
import { cn, fmtEur, fmtKm, fmtYear, tierLabel } from "@/lib/format"
import { ConfidenceBadge, DiffBadge, MetaPill } from "@/components/badges"

/**
 * Ranked batch results, most → least below market. Autobazar and Bazoš are
 * shown in SEPARATE columns and never blended — each keeps its own median,
 * price difference, and sample size, mirroring the single-car result layout.
 */
export function InventoryResultsTable({ cars }: { cars: AnalysisCarResult[] }) {
  if (cars.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-surface px-5 py-10 text-center text-sm text-muted">
        No cars produced a usable market ranking. See the per-car notes for why (insufficient comparables or
        blocked sources).
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="grid grid-cols-[2.5rem_minmax(0,1fr)_7rem_9rem_9rem_9rem] items-center gap-3 border-b border-border bg-surface-2/50 px-4 py-2.5 text-[11px] font-medium uppercase tracking-wider text-faint">
        <span className="text-center">#</span>
        <span>Vehicle</span>
        <span className="text-right">Asking</span>
        <span>Autobazar.eu</span>
        <span>Bazoš.sk</span>
        <span>Confidence</span>
      </div>
      <ul>
        {cars.map((car, i) => (
          <ResultRow key={car.row_index} car={car} rank={i + 1} />
        ))}
      </ul>
    </div>
  )
}

function ResultRow({ car, rank }: { car: AnalysisCarResult; rank: number }) {
  const [open, setOpen] = useState(false)
  const specs = [fmtYear(car.year), car.fuel, car.km != null ? fmtKm(car.km) : null]
    .filter(Boolean)
    .join(" · ")

  return (
    <li className="border-b border-border last:border-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="grid w-full grid-cols-[2.5rem_minmax(0,1fr)_7rem_9rem_9rem_9rem] items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2/40"
      >
        <span className="text-center font-mono text-[13px] tabular-nums text-faint">{rank}</span>
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-sm font-medium text-foreground">
            {car.brand} {car.model}
            {car.variant ? <span className="text-muted"> {car.variant}</span> : null}
          </span>
          <span className="truncate text-[12px] text-faint">{specs || "—"}</span>
        </span>
        <span className="text-right font-mono text-sm tabular-nums text-foreground">
          {fmtEur(car.asking_price_eur)}
        </span>
        <SourceCell src={car.autobazar} />
        <SourceCell src={car.bazos} />
        <span className="flex items-center gap-2">
          <ConfidenceBadge flag={car.confidence_flag} />
          <svg
            viewBox="0 0 24 24"
            className={cn("h-4 w-4 shrink-0 text-faint transition-transform", open && "rotate-180")}
            fill="none"
            aria-hidden
          >
            <path d="m6 9 6 6 6-6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </button>

      {open && (
        <div className="grid gap-4 border-t border-border bg-surface-2/30 px-4 py-4 md:grid-cols-2">
          <SourceDetail title="Autobazar.eu" src={car.autobazar} />
          <SourceDetail title="Bazoš.sk" src={car.bazos} />
          <div className="md:col-span-2 flex flex-col gap-1 border-t border-border pt-3 text-[12px] text-faint">
            <p>
              <span className="text-muted">Why this confidence: </span>
              {car.confidence_reasons}
            </p>
            {car.median_spread_pct != null && (
              <p>
                <span className="text-muted">Cross-source median spread: </span>
                {car.median_spread_pct}% (shown, never averaged)
              </p>
            )}
            {car.missing_critical_fields && car.missing_critical_fields !== "none" && (
              <p>
                <span className="text-muted">Missing inventory fields: </span>
                {car.missing_critical_fields}
              </p>
            )}
          </div>
        </div>
      )}
    </li>
  )
}

function SourceCell({ src }: { src: AnalysisSourceResult }) {
  if (src.error && src.comparable_count === 0) {
    return <span className="text-[12px] text-faint">{src.error.startsWith("BLOCKED") ? "blocked" : "no data"}</span>
  }
  if (src.comparable_count === 0) {
    return <span className="text-[12px] text-faint">—</span>
  }
  return (
    <span className="flex flex-col items-start gap-1">
      <span className="font-mono text-[13px] tabular-nums text-foreground">{fmtEur(src.median_eur)}</span>
      <span className="flex items-center gap-1.5">
        <DiffBadge pct={src.price_diff_pct} />
        <span className="text-[11px] text-faint">n={src.comparable_count}</span>
      </span>
    </span>
  )
}

function SourceDetail({ title, src }: { title: string; src: AnalysisSourceResult }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-foreground">{title}</span>
        <div className="flex items-center gap-1.5">
          {src.tier && <MetaPill>{tierLabel(src.tier)}</MetaPill>}
          <MetaPill tone={src.insufficient ? "caution" : "neutral"}>n={src.comparable_count}</MetaPill>
        </div>
      </div>
      {src.error ? (
        <p className="text-[12px] text-faint">{src.error}</p>
      ) : src.comparable_count === 0 ? (
        <p className="text-[12px] text-faint">No comparable listings.</p>
      ) : (
        <div className="flex items-center gap-2 text-[13px]">
          <span className="text-muted">Median</span>
          <span className="font-mono tabular-nums text-foreground">{fmtEur(src.median_eur)}</span>
          <DiffBadge pct={src.price_diff_pct} />
        </div>
      )}
      {src.example_links.length > 0 && (
        <div className="flex flex-col gap-0.5">
          {src.example_links.slice(0, 3).map((href, i) => (
            <a
              key={i}
              href={href}
              target="_blank"
              rel="noreferrer"
              className="truncate text-[12px] text-accent hover:underline"
            >
              {href}
            </a>
          ))}
        </div>
      )}
    </div>
  )
}
