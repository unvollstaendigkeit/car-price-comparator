"use client"

import { useEffect, useState } from "react"
import { useSearchParams } from "next/navigation"
import Link from "next/link"
import type { AnalysisCarResult, AnalysisSummary } from "@/lib/types"
import { loadInventoryResult } from "@/lib/inventory-result-storage"
import { exportInventoryXlsx, exportInventoryReport } from "@/lib/exports"
import { useT } from "@/lib/i18n/use-t"
import { InventoryResultsTable } from "@/components/inventory-results-table"
import { SummaryStat } from "@/components/inventory-module"

/**
 * The /inventory-result page's body. Seeded from localStorage (see
 * lib/inventory-result-storage.ts's own docstring for why - NOT a
 * shareable link, only readable in the browser that ran the analysis).
 * Deliberately leaner than the live AnalysisPanel this data came from: no
 * retrieval log, no per-model cache timings - just the finished, ranked
 * result, the same way SingleCarResult on /result is a clean summary
 * rather than a replay of the live progress stream.
 */
export function InventoryResultView() {
  const t = useT()
  const searchParams = useSearchParams()
  const id = searchParams.get("id")
  const [data, setData] = useState<{ ranked: AnalysisCarResult[]; summary: AnalysisSummary | null } | null | undefined>(
    undefined,
  )

  useEffect(() => {
    setData(id ? loadInventoryResult(id) : null)
  }, [id])

  useEffect(() => {
    document.title = t.inventoryResultPage.pageTitle
  }, [t])

  if (data === undefined) {
    return <div className="h-24 animate-pulse rounded-lg border border-border bg-surface" />
  }

  if (data === null) {
    return (
      <div className="flex flex-col items-start gap-3 rounded-lg border border-border bg-surface px-5 py-6">
        <p className="text-[15px] text-foreground">{t.inventoryResultPage.notFoundTitle}</p>
        <p className="text-[13px] text-muted">{t.inventoryResultPage.notFoundBody}</p>
        <Link href="/" className="text-[13px] font-medium text-accent underline-offset-2 hover:underline">
          {t.inventoryResultPage.backLink}
        </Link>
      </div>
    )
  }

  const { ranked, summary } = data

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-5 md:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-2xl font-semibold text-foreground">{t.count.cars(ranked.length)}</h2>
          {ranked.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => exportInventoryXlsx(ranked, t)}
                title={t.inventory.analysis.exportResultsTitle}
                className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-[13px] font-semibold text-accent-foreground transition-opacity hover:opacity-90"
              >
                {t.inventory.analysis.exportResults}
              </button>
              <button
                type="button"
                onClick={() => exportInventoryReport(ranked, summary, t)}
                title={t.inventory.analysis.exportReportTitle}
                className="flex items-center gap-1.5 rounded-md border border-accent/50 px-3 py-1.5 text-[13px] font-medium text-accent transition-colors hover:bg-accent/10"
              >
                {t.inventory.analysis.exportReport}
              </button>
            </div>
          )}
        </div>

        {summary && (
          <div className="flex flex-wrap gap-2">
            <SummaryStat label={t.inventory.analysis.statHigh} value={summary.counts.high} tone="positive" />
            <SummaryStat label={t.inventory.analysis.statMedium} value={summary.counts.medium} tone="accent" />
            <SummaryStat label={t.inventory.analysis.statLow} value={summary.counts.low} tone="caution" />
            <SummaryStat label={t.inventory.analysis.statInsufficient} value={summary.counts.insufficient} tone="muted" />
          </div>
        )}
      </div>

      {ranked.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-[13px] text-faint">{t.inventory.analysis.rankedHint}</p>
          <InventoryResultsTable cars={ranked} />
        </div>
      )}
    </div>
  )
}
