"use client"

import { useRef, useState } from "react"
import type { CarInput, CompareResult, Stage } from "@/lib/types"
import { streamCompare } from "@/lib/api"
import { CarForm } from "@/components/car-form"
import { ProgressStream } from "@/components/progress-stream"
import { SingleCarResult } from "@/components/single-car-result"

/**
 * Single-car valuation workflow. Extracted verbatim from the original page so
 * the top-level page can switch between this and the Inventory module without
 * changing any single-car behavior.
 */
export function SingleCarModule() {
  const [stage, setStage] = useState<Stage | null>(null)
  const [counts, setCounts] = useState<{ autobazar?: number; bazos?: number }>({})
  const [result, setResult] = useState<CompareResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const busy = stage !== null && stage !== "result" && stage !== "error"

  function handleClear() {
    abortRef.current?.abort()
    setResult(null)
    setError(null)
    setCounts({})
    setStage(null)
  }

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
    <div className="flex flex-col gap-7">
      <CarForm onSubmit={runCompare} onClear={handleClear} busy={busy} />

      {busy && stage && <ProgressStream current={stage} counts={counts} />}

      {error && !busy && (
        <div className="rounded-lg border border-danger/40 bg-danger-soft/30 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      {result && !busy && <SingleCarResult result={result} onClear={handleClear} />}
    </div>
  )
}
