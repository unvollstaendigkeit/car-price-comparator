"use client"

import type { Stage } from "@/lib/types"
import { cn } from "@/lib/format"

interface StageInfo {
  key: string
  label: string
  startStages: Stage[]
  doneStages: Stage[]
}

const STAGES: StageInfo[] = [
  { key: "prepare", label: "Preparing vehicle", startStages: ["preparing"], doneStages: ["prepared"] },
  {
    key: "autobazar",
    label: "Searching Autobazar.eu",
    startStages: ["searching_autobazar"],
    doneStages: ["autobazar_done"],
  },
  { key: "bazos", label: "Searching Bazoš.sk", startStages: ["searching_bazos"], doneStages: ["bazos_done"] },
  { key: "compare", label: "Comparing listings", startStages: ["comparing"], doneStages: ["finalizing", "result"] },
]

const ORDER: Stage[] = [
  "preparing",
  "prepared",
  "searching_autobazar",
  "autobazar_done",
  "searching_bazos",
  "bazos_done",
  "comparing",
  "finalizing",
  "result",
]

function rank(stage: Stage): number {
  const i = ORDER.indexOf(stage)
  return i === -1 ? -1 : i
}

export function ProgressStream({
  current,
  counts,
}: {
  current: Stage
  counts: { autobazar?: number; bazos?: number }
}) {
  const currentRank = rank(current)

  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <ol className="flex flex-col gap-3">
        {STAGES.map((s) => {
          const startRank = rank(s.startStages[0])
          const doneRank = rank(s.doneStages[s.doneStages.length - 1])
          const done = currentRank >= doneRank && doneRank !== -1
          const active = !done && currentRank >= startRank

          const raw =
            s.key === "autobazar" ? counts.autobazar : s.key === "bazos" ? counts.bazos : undefined

          return (
            <li key={s.key} className="flex items-center gap-3">
              <span
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px]",
                  done && "border-positive bg-positive-soft/40 text-positive",
                  active && "border-accent text-accent",
                  !done && !active && "border-border text-faint",
                )}
                aria-hidden
              >
                {done ? "✓" : active ? <Spinner /> : ""}
              </span>
              <span
                className={cn(
                  "text-sm",
                  done && "text-foreground",
                  active && "text-foreground",
                  !done && !active && "text-faint",
                )}
              >
                {s.label}
              </span>
              {raw !== undefined && (done || active) && (
                <span className="ml-auto font-mono text-[13px] text-muted">{raw} listings</span>
              )}
            </li>
          )
        })}
      </ol>
      <p className="mt-4 border-t border-border pt-3 text-[13px] text-faint">
        Live retrieval from two marketplaces — this typically takes 20–40 seconds. Results appear the moment
        the comparison finishes.
      </p>
    </div>
  )
}

function Spinner() {
  return (
    <span className="block h-3 w-3 animate-spin rounded-full border border-accent border-t-transparent" aria-label="in progress" />
  )
}
