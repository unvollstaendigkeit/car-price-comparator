"use client"

import { useState } from "react"
import { cn } from "@/lib/format"
import { SingleCarModule } from "@/components/single-car-module"
import { InventoryModule } from "@/components/inventory-module"

type TopMode = "single" | "inventory"

export default function Page() {
  const [mode, setMode] = useState<TopMode>("single")

  return (
    <main className="mx-auto flex min-h-dvh max-w-5xl flex-col gap-7 px-4 py-8 md:px-6 md:py-10">
      <header className="flex flex-col gap-4 border-b border-border pb-6">
        <div className="flex flex-col gap-1">
          <span className="text-3xl font-semibold tracking-tight">
            <span className="text-foreground">Car</span>
            <span className="text-accent">val</span>
          </span>
          <p className="text-[15px] text-muted">
            Used-car valuation against two independent marketplaces — shown separately, never merged.
          </p>
        </div>

        <div
          role="tablist"
          aria-label="Valuation mode"
          className="inline-flex w-fit rounded-lg border border-border bg-surface p-1"
        >
          <ModeTab active={mode === "single"} onClick={() => setMode("single")}>
            Single car
          </ModeTab>
          <ModeTab active={mode === "inventory"} onClick={() => setMode("inventory")}>
            Multi-car
          </ModeTab>
        </div>
      </header>

      {mode === "single" ? <SingleCarModule /> : <InventoryModule />}
    </main>
  )
}

function ModeTab({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "rounded-md px-4 py-1.5 text-sm font-medium transition-colors",
        active ? "bg-accent text-accent-foreground" : "text-muted hover:text-foreground",
      )}
    >
      {children}
    </button>
  )
}
