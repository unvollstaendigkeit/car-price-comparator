"use client"

import type { CompareResult, SourceResult } from "@/lib/types"
import { cn, fmtEur, fmtKm, fmtPct, fmtYear, valuationTone } from "@/lib/format"
import { ConfidenceBadge } from "./badges"
import { SourceCard } from "./source-card"
import { Disclosure } from "./disclosure"

const SOURCE_LABELS: Record<string, string> = {
  autobazar: "Autobazar.eu",
  bazos: "Bazoš.sk",
}

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

export function SingleCarResult({ result }: { result: CompareResult }) {
  const { car, sources, cross_source, confidence } = result
  const agreement = cross_source.agreement ? AGREEMENT_COPY[cross_source.agreement] : null

  const primaryKey = result.primary_source_for_ranking
  const primaryPct = result.primary_undervaluation_pct
  const primaryData = primaryKey ? sources[primaryKey as "autobazar" | "bazos"] : null
  const primaryLabel = primaryKey ? SOURCE_LABELS[primaryKey] ?? primaryKey : null

  const tone = valuationTone(primaryPct)
  const toneCls = tone === "positive" ? "text-positive" : tone === "negative" ? "text-negative" : "text-foreground"
  const toneLabel = tone === "positive" ? "below market" : tone === "negative" ? "above market" : "at market"

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

      {/* 2 — The key market-position result */}
      <section className="rounded-lg border border-border bg-surface">
        <div className="px-5 pb-5 pt-4">
          <p className="text-[13px] uppercase tracking-wide text-faint">Market position</p>
          {primaryData && primaryPct !== null && primaryPct !== undefined ? (
            <div className="mt-2 flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
              <div className="flex flex-col">
                <span className={cn("font-mono text-5xl font-semibold leading-none tabular-nums", toneCls)}>
                  {fmtPct(primaryPct)}
                </span>
                <span className={cn("mt-2 text-[15px] font-medium", toneCls)}>{toneLabel}</span>
              </div>
              <div className="text-right">
                <p className="font-mono text-xl tabular-nums text-foreground">
                  {fmtEur(primaryData.median_asking_eur)}
                </p>
                <p className="text-[13px] text-faint">median asking price · via {primaryLabel}</p>
              </div>
            </div>
          ) : (
            <p className="mt-2 text-lg font-medium text-muted">
              Not enough data to estimate a reliable market position.
            </p>
          )}
        </div>

        {/* 3 — Confidence (subtle) */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border px-5 py-3">
          <ConfidenceBadge flag={confidence.flag} />
          <span className="text-sm text-muted">{coverage}</span>
          {agreement && cross_source.median_spread_pct !== null && (
            <span className={cn("text-sm", agreement.cls)}>
              · {agreement.label} ({fmtPct(cross_source.median_spread_pct, 0)} spread)
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
        <SourceCard sourceKey="autobazar" data={sources.autobazar} />
        <SourceCard sourceKey="bazos" data={sources.bazos} />
      </div>

      {/* 7 — Methodology, out of the primary hierarchy */}
      <Disclosure summary="How Carval calculates this" className="px-1">
        <p className="max-w-2xl text-[13px] leading-relaxed text-faint">
          Each marketplace is evaluated independently and shown separately — Carval never blends them into a single
          number. Percentages are relative to your asking price: a positive figure means the car is priced below
          that market&apos;s median (a potential find), negative means above it. The headline market position uses
          the marketplace with the stronger comparable sample.
        </p>
      </Disclosure>
    </div>
  )
}
