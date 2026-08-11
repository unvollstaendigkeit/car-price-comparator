"use client"

import { useRef, useState } from "react"
import type { CarInput, CompareResult, Stage } from "@/lib/types"
import { streamCompare } from "@/lib/api"
import { CarForm } from "@/components/car-form"
import { ProgressStream } from "@/components/progress-stream"
import { SingleCarResult } from "@/components/single-car-result"

export default function Page() {
  const [stage, setStage] = useState<Stage | null>(null)
  const [counts, setCounts] = useState<{ autobazar?: number; bazos?: number }>({})
  const [result, setResult] = useState<CompareResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const busy = stage !== null && stage !== "result" && stage !== "error"

  async function runCompare(input: CarInput) {
    setResult(null)
    setError(null)
    setCounts({})
    setStage("preparing")

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    try {
      await streamCompare(
        input,
        (e) => {
          setStage(e.stage)
          if (e.stage === "autobazar_done") setCounts((c) => ({ ...c, autobazar: e.raw_count }))
          if (e.stage === "bazos_done") setCounts((c) => ({ ...c, bazos: e.raw_count }))
          if (e.stage === "result" && e.result) setResult(e.result)
          if (e.stage === "error") setError(e.message ?? "Comparison failed")
        },
        controller.signal,
      )
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof Error ? err.message : "Request failed")
        setStage("error")
      }
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-5xl flex-col gap-6 px-4 py-8 md:px-6">
      <header className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-md bg-accent font-mono text-lg font-bold text-accent-foreground">
          €
        </div>
        <div>
          <h1 className="text-lg font-semibold text-foreground">Market Price Check</h1>
          <p className="text-xs text-muted">
            Used-car valuation against two independent marketplaces — shown separately, never merged.
          </p>
        </div>
      </header>

      <div className="flex flex-col gap-6">
        <CarForm onSubmit={runCompare} busy={busy} />

        {busy && stage && <ProgressStream current={stage} counts={counts} />}

        {error && !busy && (
          <div className="rounded-lg border border-danger/40 bg-danger-soft/30 px-4 py-3 text-sm text-danger">
            {error}
          </div>
        )}

        {result && !busy && <SingleCarResult result={result} />}
      </div>
    </main>
  )
}
