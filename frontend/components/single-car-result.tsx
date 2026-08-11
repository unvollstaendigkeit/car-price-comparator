"use client"

import type { CompareResult } from "@/lib/types"
import { fmtEur, fmtKm, fmtPct, fmtYear } from "@/lib/format"
import { ConfidenceBadge } from "./badges"
import { SourceCard } from "./source-card"

const AGREEMENT_COPY: Record<string, { label: string; cls: string; note: string }> = {
  agree: {
    label: "Sources agree",
    cls: "border-positive/40 bg-positive-soft/30 text-positive",
    note: "Both marketplaces produced closely aligned median prices.",
  },
  meaningful: {
    label: "Some divergence",
    cls: "border-caution/40 bg-caution/10 text-caution",
    note: "The two marketplaces differ enough to treat the estimate with care.",
  },
  large: {
    label: "Sources disagree",
    cls: "border-negative/40 bg-negative-soft/30 text-negative",
    note: "Large gap between marketplaces — inspect the comparables on each before trusting either.",
  },
}

export function SingleCarResult({ result }: { result: CompareResult }) {
  const { car, sources, cross_source, confidence } = result
  const agreement = cross_source.agreement ? AGREEMENT_COPY[cross_source.agreement] : null

  return (
    <div className="flex flex-col gap-4">
      {/* Vehicle + confidence header */}
      <div className="rounded-lg border border-border bg-surface">
        <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
          <div>
            <h2 className="text-lg font-semibold text-foreground">
              {car.brand} {car.model}
              {car.variant_engine ? (
                <span className="ml-2 font-mono text-sm font-normal text-muted">{car.variant_engine}</span>
              ) : null}
            </h2>
            <p className="mt-0.5 text-sm text-muted">
              {[fmtYear(car.year), car.fuel, fmtKm(car.km), car.transmission, car.body_type]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          <div className="text-right">
            <p className="text-[11px] uppercase tracking-wide text-faint">Asking price</p>
            <p className="font-mono text-xl tabular-nums text-foreground">{fmtEur(car.asking_price_eur)}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3 border-t border-border px-4 py-2.5">
          <ConfidenceBadge flag={confidence.flag} />
          {confidence.reasons && <span className="text-xs text-muted">{confidence.reasons}</span>}
        </div>
      </div>

      {/* Warnings — never silently swallowed */}
      {confidence.warnings.length > 0 && (
        <ul className="flex flex-col gap-1.5 rounded-lg border border-caution/40 bg-caution/5 px-4 py-3">
          {confidence.warnings.map((w, i) => (
            <li key={i} className="flex items-start gap-2 text-xs text-caution">
              <span aria-hidden className="mt-0.5">
                ⚠
              </span>
              <span>{w}</span>
            </li>
          ))}
        </ul>
      )}

      {/* Cross-source agreement banner */}
      {agreement && (
        <div className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-4 py-2.5 ${agreement.cls}`}>
          <span className="text-sm font-semibold">{agreement.label}</span>
          {cross_source.median_spread_pct !== null && (
            <span className="font-mono text-xs tabular-nums">
              {fmtPct(cross_source.median_spread_pct, 0)} median spread
            </span>
          )}
          <span className="text-xs opacity-80">{agreement.note}</span>
        </div>
      )}

      {/* The two independent sources, side by side, NEVER merged */}
      <div className="grid gap-4 lg:grid-cols-2">
        <SourceCard sourceKey="autobazar" data={sources.autobazar} />
        <SourceCard sourceKey="bazos" data={sources.bazos} />
      </div>

      <p className="px-1 text-xs text-faint">
        Each marketplace is evaluated independently and shown separately — the tool never blends them into a
        single number. Percentages are relative to the asking price: positive means priced below that
        market&apos;s median.
      </p>
    </div>
  )
}
