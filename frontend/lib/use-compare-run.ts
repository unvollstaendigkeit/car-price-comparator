"use client"

import { useRef, useState } from "react"
import type { CarInput, CompareResult, Stage } from "@/lib/types"
import { streamCompare } from "@/lib/api"
import { useT } from "@/lib/i18n/use-t"

/**
 * Drives one streamed single-car comparison. Extracted out of
 * single-car-module.tsx so the /result page (which runs the comparison in
 * its own tab, seeded from the URL) can reuse the exact same stage/result/
 * error handling instead of re-implementing it.
 */
export function useCompareRun() {
  const t = useT()
  const [stage, setStage] = useState<Stage | null>(null)
  const [counts, setCounts] = useState<{ autobazar?: number; bazos?: number }>({})
  const [result, setResult] = useState<CompareResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const busy = stage !== null && stage !== "result" && stage !== "error"

  function reset() {
    abortRef.current?.abort()
    setResult(null)
    setError(null)
    setCounts({})
    setStage(null)
  }

  async function run(input: CarInput) {
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
          if (e.stage === "error") setError(e.message ?? t.errors.comparisonFailed)
        },
        controller.signal,
      )
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof Error ? err.message : t.errors.requestFailed)
        setStage("error")
      }
    }
  }

  return { stage, counts, result, error, busy, run, reset }
}
