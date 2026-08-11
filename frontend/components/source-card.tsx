"use client"

import type { SourceResult } from "@/lib/types"
import { cn, fmtEur, fmtPct, fmtSignedEur, tierLabel, valuationTone } from "@/lib/format"
import { ComparablesTable } from "./comparables-table"
import { Disclosure } from "./disclosure"

const SOURCE_META: Record<string, { name: string; host: string }> = {
  autobazar: { name: "Autobazar.eu", host: "autobazar.eu" },
  bazos: { name: "Bazoš.sk", host: "bazos.sk" },
}

export function SourceCard({ sourceKey, data }: { sourceKey: string; data: SourceResult }) {
  const meta = SOURCE_META[sourceKey] ?? { name: sourceKey, host: "" }
  const hasRetrievalError = Boolean(data.retrieval_error)
  // Only a genuinely empty result gets the placeholder. Any listing at all is
  // shown — a thin sample is surfaced with a concise caution, never hidden.
  const hasNoComparables = !hasRetrievalError && data.comparable_count === 0
  const isThinSample = data.insufficient || data.comparable_count < 4

  const pct = data.undervaluation_pct
  const tone = valuationTone(pct)
  const toneCls = tone === "positive" ? "text-positive" : tone === "negative" ? "text-negative" : "text-foreground"
  const toneLabel = tone === "positive" ? "below market" : tone === "negative" ? "above market" : "at market"

  return (
    <section className="flex flex-col rounded-lg border border-border bg-surface" aria-label={`${meta.name} result`}>
      {/* Header */}
      <header className="flex items-center justify-between gap-3 border-b border-border px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <span className="h-2 w-2 rounded-full bg-accent" aria-hidden />
          <h3 className="text-[15px] font-semibold text-foreground">{meta.name}</h3>
        </div>
        {!hasRetrievalError && (
          <span className="text-[13px] text-faint">
            {data.comparable_count} comparable{data.comparable_count === 1 ? "" : "s"}
          </span>
        )}
      </header>

      {/* Body */}
      {hasRetrievalError ? (
        <div className="flex flex-1 flex-col gap-3 px-5 py-6">
          <div>
            <p className="text-lg font-semibold text-danger">Couldn&apos;t fetch listings</p>
            <p className="mt-1 text-sm text-muted">
              This is a temporary fetch problem, not an absence of comparable cars.
            </p>
          </div>
          <Disclosure summary="Show error detail">
            <p className="text-[13px] text-faint">{data.retrieval_error}</p>
          </Disclosure>
        </div>
      ) : hasNoComparables ? (
        <div className="flex flex-1 flex-col gap-2 px-5 py-6">
          <p className="text-lg font-semibold text-muted">No comparable cars found</p>
          <p className="text-sm text-faint">
            No listings matched this exact spec on {meta.name}. This is an absence of matching cars, not a fetch
            failure.
          </p>
        </div>
      ) : (
        <>
          {/* Hero: the key market-position number */}
          <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 px-5 py-5">
            {pct !== null && pct !== undefined ? (
              <div className="flex flex-col">
                <span className={cn("font-mono text-4xl font-semibold leading-none tabular-nums", toneCls)}>
                  {fmtPct(pct)}
                </span>
                <span className={cn("mt-1.5 text-sm font-medium", toneCls)}>{toneLabel}</span>
              </div>
            ) : (
              <div className="flex flex-col">
                <span className="font-mono text-3xl font-semibold leading-none tabular-nums text-foreground">
                  {fmtEur(data.median_asking_eur)}
                </span>
                <span className="mt-1.5 text-sm text-faint">market median</span>
              </div>
            )}

            <div className="flex flex-col items-end text-right">
              {pct !== null && pct !== undefined && (
                <span className="font-mono text-lg tabular-nums text-foreground">
                  {fmtEur(data.median_asking_eur)}
                </span>
              )}
              <span className="text-[13px] text-faint">median asking price</span>
              {data.price_difference_eur !== null && data.price_difference_eur !== undefined && (
                <span className="mt-0.5 font-mono text-[13px] tabular-nums text-muted">
                  {fmtSignedEur(data.price_difference_eur)} vs. asking
                </span>
              )}
            </div>
          </div>

          {/* Concise sample caution */}
          {isThinSample && (
            <p className="flex items-center gap-1.5 border-t border-border bg-caution/10 px-5 py-2.5 text-[13px] text-caution">
              <span aria-hidden>⚠</span>
              Small sample — {data.comparable_count} comparable car{data.comparable_count === 1 ? "" : "s"}
            </p>
          )}

          {/* Transparency, tucked away */}
          <div className="flex flex-col gap-2.5 border-t border-border px-5 py-3.5">
            <Disclosure summary="Comparison details">
              <dl className="flex flex-col gap-1.5 text-[13px]">
                <Row label="Match tier" value={tierLabel(data.tier)} />
                <Row
                  label="Price range (P25–P75)"
                  value={`${fmtEur(data.market_p25_eur)} – ${fmtEur(data.market_p75_eur)}`}
                />
                <Row label="Comparable cars" value={String(data.comparable_count)} />
                {data.outliers_trimmed > 0 && (
                  <Row label="Outliers trimmed" value={String(data.outliers_trimmed)} />
                )}
                {data.unknown_year_km_frac > 0 && (
                  <Row label="Missing year/km" value={fmtPct(data.unknown_year_km_frac * 100, 0)} />
                )}
                {data.sample_warning && <p className="mt-1 text-caution">{data.sample_warning}</p>}
              </dl>
            </Disclosure>

            <Disclosure summary={`Show comparable listings (${data.comparables.length})`} tone="accent">
              <div className="-mx-5 border-t border-border">
                <ComparablesTable rows={data.comparables} />
              </div>
            </Disclosure>
          </div>
        </>
      )}
    </section>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-faint">{label}</dt>
      <dd className="font-mono tabular-nums text-muted">{value}</dd>
    </div>
  )
}
