"use client"

import { useEffect, useRef } from "react"
import { useSearchParams } from "next/navigation"
import Link from "next/link"
import { carInputFromSearchParams } from "@/lib/car-input-url"
import { useCompareRun } from "@/lib/use-compare-run"
import { useT } from "@/lib/i18n/use-t"
import { ProgressStream } from "@/components/progress-stream"
import { SingleCarResult } from "@/components/single-car-result"

/**
 * The /result page's body. Seeded entirely from the URL's query params (the
 * car's inputs) rather than from any state handed over by the tab that
 * opened it — visiting or reloading this URL re-runs a fresh live
 * comparison, same as submitting the form does.
 */
export function ResultView() {
  const t = useT()
  const searchParams = useSearchParams()
  const car = carInputFromSearchParams(searchParams)
  const { stage, counts, result, error, busy, run } = useCompareRun()

  // Guards against React StrictMode's double-invoked effect in dev, which
  // would otherwise fire the live scrape twice for one page load.
  const startedRef = useRef(false)

  useEffect(() => {
    if (!car || startedRef.current) return
    startedRef.current = true
    run(car)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (car) document.title = `${car.brand} ${car.model} — Carval`
  }, [car])

  if (!car) {
    return (
      <div className="flex flex-col items-start gap-3 rounded-lg border border-border bg-surface px-5 py-6">
        <p className="text-[15px] text-foreground">{t.resultPage.missingTitle}</p>
        <p className="text-[13px] text-muted">{t.resultPage.missingBody}</p>
        <Link href="/" className="text-[13px] font-medium text-accent underline-offset-2 hover:underline">
          {t.resultPage.backLink}
        </Link>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-7">
      {busy && stage && <ProgressStream current={stage} counts={counts} />}

      {error && !busy && (
        <div className="rounded-lg border border-danger/40 bg-danger-soft/30 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      {result && !busy && <SingleCarResult result={result} vin={car.vin} />}
    </div>
  )
}
