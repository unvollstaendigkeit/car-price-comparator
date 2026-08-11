"use client"

import type { SourceResult } from "@/lib/types"
import { fmtEur, fmtPct, fmtSignedEur, tierLabel } from "@/lib/format"
import { ComparablesTable } from "./comparables-table"
import { DiffBadge } from "./badges"

const SOURCE_META: Record<string, { name: string; host: string }> = {
  autobazar: { name: "Autobazar.eu", host: "autobazar.eu" },
  bazos: { name: "Bazoš.sk", host: "bazos.sk" },
}

export function SourceCard({ sourceKey, data }: { sourceKey: string; data: SourceResult }) {
  const meta = SOURCE_META[sourceKey] ?? { name: sourceKey, host: "" }
  const hasRetrievalError = Boolean(data.retrieval_error)
  const isInsufficient = data.insufficient

  return (
    <section className="flex flex-col rounded-lg border border-border bg-surface" aria-label={`${meta.name} result`}>
      {/* Header */}
      <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-accent" aria-hidden />
          <div>
            <h3 className="text-sm font-semibold text-foreground">{meta.name}</h3>
            <p className="text-xs text-faint">{meta.host}</p>
          </div>
        </div>
        <span className="rounded border border-border px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-muted">
          {tierLabel(data.tier)} · n={data.comparable_count}
        </span>
      </header>

      {/* Body */}
      {hasRetrievalError ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 py-10 text-center">
          <span className="rounded-full border border-danger/40 bg-danger-soft/40 px-3 py-1 text-xs font-medium text-danger">
            Retrieval failed
          </span>
          <p className="max-w-sm text-sm text-muted">{data.retrieval_error}</p>
          <p className="text-xs text-faint">This is a fetch failure, not an absence of comparable cars.</p>
        </div>
      ) : isInsufficient ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 py-10 text-center">
          <span className="rounded-full border border-border bg-surface-2 px-3 py-1 text-xs font-medium text-faint">
            Insufficient sample
          </span>
          <p className="max-w-sm text-sm text-muted">
            Only {data.comparable_count} comparable{data.comparable_count === 1 ? "" : "s"} found — too few to
            estimate a reliable market price for this exact spec.
          </p>
        </div>
      ) : (
        <>
          {/* Headline metrics */}
          <div className="grid grid-cols-2 gap-px border-b border-border bg-border">
            <Metric label="Market median" value={fmtEur(data.median_asking_eur)} />
            <Metric
              label="vs. asking price"
              value={
                <span className="flex items-center gap-2">
                  {fmtSignedEur(data.price_difference_eur)}
                  <DiffBadge pct={data.undervaluation_pct} />
                </span>
              }
            />
          </div>

          {/* Range + quality row */}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-b border-border px-4 py-2.5 text-xs text-faint">
            <span>
              P25–P75:{" "}
              <span className="font-mono text-muted">
                {fmtEur(data.market_p25_eur)} – {fmtEur(data.market_p75_eur)}
              </span>
            </span>
            {data.outliers_trimmed > 0 && <span>{data.outliers_trimmed} outlier(s) trimmed</span>}
            {data.unknown_year_km_frac > 0 && (
              <span>{fmtPct(data.unknown_year_km_frac * 100, 0)} missing year/km</span>
            )}
          </div>

          {data.sample_warning && (
            <p className="border-b border-border bg-caution/10 px-4 py-2 text-xs text-caution">
              {data.sample_warning}
            </p>
          )}

          <ComparablesTable rows={data.comparables} />
        </>
      )}
    </section>
  )
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="bg-surface px-4 py-3">
      <p className="text-[11px] uppercase tracking-wide text-muted">{label}</p>
      <p className="mt-1 font-mono text-lg tabular-nums text-foreground">{value}</p>
    </div>
  )
}
