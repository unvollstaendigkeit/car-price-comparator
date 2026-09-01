"use client"

import type { SourceResult } from "@/lib/types"
import { cn, fmtEur, fmtKm, fmtPct, fmtPctPlain, tierLabel, valuationTone } from "@/lib/format"
import { sampleWarningText } from "@/lib/warning-copy"
import { useT } from "@/lib/i18n/use-t"
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
  const t = useT()
  const meta = SOURCE_META[sourceKey] ?? { name: sourceKey, host: "" }
  const hasRetrievalError = Boolean(data.retrieval_issue)
  // Only a genuinely empty result gets the placeholder. Any listing at all is
  // shown — a thin sample is surfaced with a concise caution, never hidden.
  const hasNoComparables = !hasRetrievalError && data.comparable_count === 0
  const isThinSample = data.insufficient || data.comparable_count < 4

  const pct = data.undervaluation_pct
  const tone = valuationTone(pct)
  const toneCls = tone === "positive" ? "text-positive" : tone === "negative" ? "text-negative" : "text-muted"
  const toneLabel = tone === "positive" ? t.diff.belowMarket : tone === "negative" ? t.diff.aboveMarket : t.diff.atMarket
  // Median relative to the user's asking price. Below market => median sits
  // above asking; above market => median sits below asking. Uses existing
  // values only (magnitude + tone-derived direction), no recalculation.
  const diffAbs = data.price_difference_eur !== null && data.price_difference_eur !== undefined
    ? Math.abs(data.price_difference_eur)
    : null
  const diffDir = tone === "positive" ? t.card.aboveAsking : tone === "negative" ? t.card.belowAsking : t.card.vsAsking

  return (
    <section className="flex flex-col rounded-lg border border-border bg-surface" aria-label={t.card.resultAriaLabel(meta.name)}>
      {/* Header */}
      <header className="flex items-center justify-between gap-3 border-b border-border px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <span className="h-2 w-2 rounded-full bg-accent" aria-hidden />
          <h3 className="text-[15px] font-semibold text-foreground">{meta.name}</h3>
        </div>
        {!hasRetrievalError && <span className="text-[13px] text-faint">{t.count.comparables(data.comparable_count)}</span>}
      </header>

      {/* Body */}
      {hasRetrievalError ? (
        <div className="flex flex-1 flex-col gap-3 px-5 py-6">
          <div>
            <p className="text-lg font-semibold text-danger">{t.card.fetchFailedTitle}</p>
            <p className="mt-1 text-sm text-muted">
              {data.retrieval_issue?.reason === "timeout"
                ? t.card.fetchFailedTimeout(meta.name)
                : t.card.fetchFailedBlocked(meta.name)}
            </p>
          </div>
          {data.retrieval_error && (
            <Disclosure summary={t.card.showErrorDetail}>
              <p className="text-[13px] text-faint">{data.retrieval_error}</p>
            </Disclosure>
          )}
        </div>
      ) : hasNoComparables ? (
        <div className="flex flex-1 flex-col gap-2 px-5 py-6">
          <p className="text-lg font-semibold text-muted">{t.card.noneFoundTitle}</p>
          <p className="text-sm text-faint">{t.card.noneFoundBody(meta.name)}</p>
        </div>
      ) : (
        <>
          {/* Hero: the median asking price is the centerpiece */}
          <div className="px-5 py-5">
            <p className="text-[13px] uppercase tracking-wide text-faint">{t.card.medianAskingPrice}</p>
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
                    <span className="text-faint"> · {t.card.yourAsking} {fmtEur(askingPriceEur)}</span>
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
              {t.card.smallSample} — {t.count.comparables(data.comparable_count)}
            </p>
          )}

          {/* Transparency, tucked away */}
          <div className="flex flex-col gap-2.5 border-t border-border px-5 py-3.5">
            <Disclosure summary={t.card.comparisonDetails}>
              <dl className="flex flex-col gap-1.5 text-[13px]">
                <Row label={t.card.matchTier} value={tierLabel(data.tier, t.tier)} />
                <Row
                  label={t.card.priceRange}
                  value={`${fmtEur(data.market_p25_eur)} – ${fmtEur(data.market_p75_eur)}`}
                />
                {data.mileage && data.mileage.comp_km_median !== null && (
                  <>
                    <Row label={t.card.comparableMileageMedian} value={fmtKm(data.mileage.comp_km_median)} />
                    <Row
                      label={t.card.comparableMileageRange}
                      value={`${fmtKm(data.mileage.comp_km_p25)} – ${fmtKm(data.mileage.comp_km_p75)}`}
                    />
                    <Row label={t.card.thisCarsMileage} value={fmtKm(data.mileage.submitted_km)} />
                  </>
                )}
                <Row label={t.card.comparableCars} value={String(data.comparable_count)} />
                {data.outliers_trimmed > 0 && (
                  <Row label={t.card.outliersTrimmed} value={String(data.outliers_trimmed)} />
                )}
                {data.unknown_year_km_frac > 0 && (
                  <Row label={t.card.missingYearKm} value={fmtPct(data.unknown_year_km_frac * 100, 0)} />
                )}
                {data.sample_warnings.map((w) => (
                  <p key={w.code} className="mt-1 text-caution">
                    {sampleWarningText(t, w)}
                  </p>
                ))}
              </dl>
            </Disclosure>

            <Disclosure summary={t.card.showComparableListings(data.comparables.length)} tone="accent">
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
