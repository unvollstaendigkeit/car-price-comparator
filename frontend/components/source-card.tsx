"use client"

import type { SourceResult } from "@/lib/types"
import { cn, fmtEur, fmtKm, fmtPct, fmtPctPlain, tierLabel, valuationTone } from "@/lib/format"
import { ComparablesTable } from "./comparables-table"
import { Disclosure } from "./disclosure"
import { MileageNotice } from "./mileage-notice"

const SOURCE_META: Record<string, { name: string; host: string }> = {
  autobazar: { name: "Autobazar.eu", host: "autobazar.eu" },
  bazos: { name: "Bazoš.sk", host: "bazos.sk" },
}

export function SourceCard({
  sourceKey,
  data,
  askingPriceEur,
}: {
  sourceKey: string
  data: SourceResult
  askingPriceEur?: number | null
}) {
  const meta = SOURCE_META[sourceKey] ?? { name: sourceKey, host: "" }
  const hasRetrievalError = Boolean(data.retrieval_error)
  // Only a genuinely empty result gets the placeholder. Any listing at all is
  // shown — a thin sample is surfaced with a concise caution, never hidden.
  const hasNoComparables = !hasRetrievalError && data.comparable_count === 0
  const isThinSample = data.insufficient || data.comparable_count < 4

  const pct = data.undervaluation_pct
  const tone = valuationTone(pct)
  const toneCls = tone === "positive" ? "text-positive" : tone === "negative" ? "text-negative" : "text-muted"
  const toneLabel = tone === "positive" ? "below market" : tone === "negative" ? "above market" : "at market"
  // Median relative to the user's asking price. Below market => median sits
  // above asking; above market => median sits below asking. Uses existing
  // values only (magnitude + tone-derived direction), no recalculation.
  const diffAbs = data.price_difference_eur !== null && data.price_difference_eur !== undefined
    ? Math.abs(data.price_difference_eur)
    : null
  const diffDir = tone === "positive" ? "above your asking price" : tone === "negative" ? "below your asking price" : "vs. your asking price"

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
          {/* Hero: the median asking price is the centerpiece */}
          <div className="px-5 py-5">
            <p className="text-[13px] uppercase tracking-wide text-faint">Median asking price</p>
            <p className="mt-1 font-mono text-4xl font-semibold leading-none tabular-nums text-foreground">
              {fmtEur(data.median_asking_eur)}
            </p>

            {pct !== null && pct !== undefined && (
              <div className="mt-3 flex flex-col gap-0.5">
                {diffAbs !== null && (
                  <span className={cn("text-[18px] font-semibold", toneCls)}>
                    {fmtEur(diffAbs)} {diffDir}
                  </span>
                )}
                <span className="text-[15px] text-muted">
                  <span className={toneCls}>{fmtPctPlain(pct)} {toneLabel}</span>
                  {askingPriceEur !== null && askingPriceEur !== undefined && (
                    <span className="text-faint"> · your asking {fmtEur(askingPriceEur)}</span>
                  )}
                </span>
              </div>
            )}

            {/* Mileage-similarity signal — sits right under the valuation so a
                mileage mismatch is impossible to miss. Per source; never merged. */}
            {data.mileage && (
              <MileageNotice
                className="mt-4"
                match={data.mileage_match}
                compMedian={data.mileage.comp_km_median}
                compP25={data.mileage.comp_km_p25}
                compP75={data.mileage.comp_km_p75}
                submittedKm={data.mileage.submitted_km}
                direction={data.mileage.direction}
                note={data.mileage.note}
              />
            )}
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
                {data.mileage && data.mileage.comp_km_median !== null && (
                  <>
                    <Row label="Comparable mileage (median)" value={fmtKm(data.mileage.comp_km_median)} />
                    <Row
                      label="Comparable mileage (P25–P75)"
                      value={`${fmtKm(data.mileage.comp_km_p25)} – ${fmtKm(data.mileage.comp_km_p75)}`}
                    />
                    <Row label="This car's mileage" value={fmtKm(data.mileage.submitted_km)} />
                  </>
                )}
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
