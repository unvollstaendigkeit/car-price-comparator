"use client"

import type { CompareResult, SourceResult } from "@/lib/types"
import { cn, fmtEur, fmtKm, fmtPctPlain, fmtYear } from "@/lib/format"
import { exportSingleCarReport } from "@/lib/exports"
import { ConfidenceBadge } from "./badges"
import { SourceCard } from "./source-card"
import { Disclosure } from "./disclosure"

const AGREEMENT_COPY: Record<string, { label: string; cls: string; note: string }> = {
  agree: {
    label: "sources agree",
    cls: "text-positive",
    note: "Both marketplaces produced closely aligned median prices.",
  },
  meaningful: {
    label: "limited agreement",
    cls: "text-caution",
    note: "The two marketplaces differ enough to treat the estimate with care.",
  },
  large: {
    label: "sources disagree",
    cls: "text-negative",
    note: "Large gap between marketplaces — inspect the comparables on each before trusting either.",
  },
}

function isUsable(s: SourceResult): boolean {
  return !s.retrieval_error && !s.insufficient && s.comparable_count > 0 && s.undervaluation_pct !== null
}

export function SingleCarResult({ result, onClear }: { result: CompareResult; onClear?: () => void }) {
  const { car, sources, cross_source, confidence } = result
  const agreement = cross_source.agreement ? AGREEMENT_COPY[cross_source.agreement] : null

  // Human one-liner about evidence coverage (derived from existing flags only).
  const abUsable = isUsable(sources.autobazar)
  const bzUsable = isUsable(sources.bazos)
  const coverage =
    abUsable && bzUsable
      ? "Both marketplaces had enough comparable cars to estimate the market."
      : abUsable
        ? "Only Autobazar.eu had enough comparable cars to estimate the market."
        : bzUsable
          ? "Only Bazoš.sk had enough comparable cars to estimate the market."
          : "Neither marketplace had enough comparable cars for a reliable estimate."

  const specLine = [fmtYear(car.year), car.fuel, fmtKm(car.km), car.transmission, car.body_type]
    .filter(Boolean)
    .join(" · ")

  return (
    <div className="flex flex-col gap-5">
      {/* 0 — Result actions: secondary to the valuation, but obvious */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[13px] uppercase tracking-wide text-faint">Valuation result</p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => exportSingleCarReport(result)}
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
            Export result
          </button>
          {onClear && (
            <button
              type="button"
              onClick={onClear}
              className="rounded-md border border-border px-3 py-1.5 text-[13px] font-medium text-muted transition-colors hover:border-border-strong hover:text-foreground"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {/* 1 — The car */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-foreground">
            {car.brand} {car.model}
            {car.variant_engine ? (
              <span className="ml-2 font-mono text-base font-normal text-muted">{car.variant_engine}</span>
            ) : null}
          </h2>
          {specLine && <p className="mt-1 text-[15px] text-muted">{specLine}</p>}
        </div>
        <div className="text-right">
          <p className="text-[13px] uppercase tracking-wide text-faint">Asking price</p>
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
              · {agreement.label} ({fmtPctPlain(cross_source.median_spread_pct, 0)} spread)
            </span>
          )}
        </div>

        {(confidence.reasons || confidence.warnings.length > 0 || agreement) && (
          <div className="border-t border-border px-5 py-3">
            <Disclosure summary="Why this confidence?">
              <div className="flex flex-col gap-2 text-[13px] text-muted">
                {confidence.reasons && <p>{confidence.reasons}</p>}
                {confidence.warnings.length > 0 && (
                  <ul className="flex flex-col gap-1">
                    {confidence.warnings.map((w, i) => (
                      <li key={i} className="flex items-start gap-1.5 text-caution">
                        <span aria-hidden className="mt-0.5">
                          ⚠
                        </span>
                        <span>{w}</span>
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

      {/* 7 — Methodology, out of the primary hierarchy */}
      <Disclosure summary="How Carval calculates this" className="px-1">
        <p className="max-w-2xl text-[13px] leading-relaxed text-faint">
          Each marketplace is evaluated independently and shown separately — Carval never blends them into a single
          number. &quot;Below market&quot; means the car is priced under that market&apos;s median asking price (a
          potential find); &quot;above market&quot; means it&apos;s priced higher. The headline figure uses the
          marketplace with the stronger comparable sample.
        </p>
      </Disclosure>
    </div>
  )
}
