"use client"

import type { CompareResult, SourceResult } from "@/lib/types"
import { SOURCE_META } from "@/lib/types"
import { cn, fmtEur, fmtKm, fmtPctPlain, fmtYear, mapLabel } from "@/lib/format"
import { exportSingleCarReport } from "@/lib/exports"
import { confidenceWarningText } from "@/lib/warning-copy"
import { tierSentence } from "@/lib/tier-sentence"
import { useT } from "@/lib/i18n/use-t"
import { ConfidenceBadge } from "./badges"
import { SourceCard } from "./source-card"
import { Disclosure } from "./disclosure"

function isUsable(s: SourceResult): boolean {
  return !s.retrieval_issue && !s.insufficient && s.comparable_count > 0 && s.undervaluation_pct !== null
}

export function SingleCarResult({
  result,
  vin,
  onClear,
}: {
  result: CompareResult
  /** Not part of the backend's CarEcho — passed through separately from what was submitted. */
  vin?: string
  onClear?: () => void
}) {
  const t = useT()
  const { car, sources, cross_source, confidence } = result
  const AGREEMENT_CLS: Record<string, string> = { agree: "text-positive", meaningful: "text-caution", large: "text-negative" }
  const agreement = cross_source.agreement
    ? { ...t.result.agreement[cross_source.agreement], cls: AGREEMENT_CLS[cross_source.agreement] }
    : null

  // Human one-liner about evidence coverage (derived from existing flags only).
  const abUsable = isUsable(sources.autobazar)
  const bzUsable = isUsable(sources.bazos)
  const coverage =
    abUsable && bzUsable
      ? t.result.coverage.both
      : abUsable
        ? t.result.coverage.onlyAutobazar
        : bzUsable
          ? t.result.coverage.onlyBazos
          : t.result.coverage.neither

  const specLine = [
    fmtYear(car.year),
    mapLabel(car.fuel, t.form.fuelLabels),
    fmtKm(car.km),
    mapLabel(car.body_type, t.form.bodyTypeLabels),
    vin,
    mapLabel(car.transmission, t.form.transmissionLabels),
  ]
    .filter(Boolean)
    .join(" · ")

  const tierSentences = [
    tierSentence(t, SOURCE_META.autobazar.label, sources.autobazar),
    tierSentence(t, SOURCE_META.bazos.label, sources.bazos),
  ].filter((s): s is string => s !== null)

  return (
    <div className="flex flex-col gap-5">
      {/* 0 — Result actions: secondary to the valuation, but obvious */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[13px] uppercase tracking-wide text-faint">{t.result.label}</p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => exportSingleCarReport(result, t)}
            className="inline-flex items-center gap-1.5 rounded-md border border-accent/50 px-3 py-1.5 text-[13px] font-medium text-accent transition-colors hover:bg-accent/10"
          >
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" aria-hidden="true">
              <path
                d="M12 3v12m0 0 4-4m-4 4-4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            {t.result.export}
          </button>
          {onClear && (
            <button
              type="button"
              onClick={onClear}
              className="rounded-md border border-border px-3 py-1.5 text-[13px] font-medium text-muted transition-colors hover:border-border-strong hover:text-foreground"
            >
              {t.result.clear}
            </button>
          )}
        </div>
      </div>

      {/* 1 — The car */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-foreground">
            {car.brand} {car.model}
            {car.variant || car.variant_engine ? (
              <span className="ml-2 font-mono text-base font-normal text-muted">
                {car.variant || car.variant_engine}
              </span>
            ) : null}
          </h2>
          {specLine && <p className="mt-1 text-[15px] text-muted">{specLine}</p>}
        </div>
        <div className="text-right">
          <p className="text-[13px] uppercase tracking-wide text-faint">{t.result.askingPrice}</p>
          <p className="font-mono text-2xl tabular-nums text-foreground">{fmtEur(car.asking_price_eur)}</p>
        </div>
      </div>

      {/* 2 — Confidence in the estimate (headline number lives in each source card below) */}
      <section className="rounded-lg border border-border bg-surface">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-4">
          <ConfidenceBadge flag={confidence.flag} />
          <span className="text-sm text-muted">{coverage}</span>
          {agreement && cross_source.median_spread_pct !== null && (
            <span className={cn("text-sm", agreement.cls)}>
              · {agreement.label} ({fmtPctPlain(cross_source.median_spread_pct, 0)} {t.result.spread})
            </span>
          )}
        </div>

        {(tierSentences.length > 0 || confidence.warnings.length > 0 || agreement) && (
          <div className="border-t border-border px-5 py-3">
            <Disclosure summary={t.result.whyConfidence}>
              <div className="flex flex-col gap-2 text-[13px] text-muted">
                {tierSentences.length > 0 && (
                  <ul className="flex flex-col gap-1">
                    {tierSentences.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ul>
                )}
                {confidence.warnings.length > 0 && (
                  <ul className="flex flex-col gap-1">
                    {confidence.warnings.map((w, i) => (
                      <li key={i} className="flex items-start gap-1.5 text-caution">
                        <span aria-hidden className="mt-0.5">
                          ⚠
                        </span>
                        <span>{confidenceWarningText(t, w)}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {agreement && <p className="text-faint">{agreement.note}</p>}
              </div>
            </Disclosure>
          </div>
        )}
      </section>

      {/* 4 & 5 — The two independent sources, side by side, NEVER merged */}
      <div className="grid gap-5 lg:grid-cols-2">
        <SourceCard sourceKey="autobazar" data={sources.autobazar} askingPriceEur={car.asking_price_eur} />
        <SourceCard sourceKey="bazos" data={sources.bazos} askingPriceEur={car.asking_price_eur} />
      </div>
    </div>
  )
}
