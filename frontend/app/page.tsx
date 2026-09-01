"use client"

import { useState } from "react"
import { cn } from "@/lib/format"
import { useT } from "@/lib/i18n/use-t"
import { SingleCarModule } from "@/components/single-car-module"
import { InventoryModule } from "@/components/inventory-module"
import { LocaleSwitcher } from "@/components/locale-switcher"

type TopMode = "single" | "inventory"

export default function Page() {
  const [mode, setMode] = useState<TopMode>("single")
  // Bumped on every logo click so <SingleCarModule> remounts (clearing its
  // form state) even when we're already on the "single" tab and switching
  // modes alone would be a no-op with zero visible effect - see the logo's
  // onClick below.
  const [resetKey, setResetKey] = useState(0)
  const t = useT()

  function goHome() {
    setMode("single")
    setResetKey((k) => k + 1)
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-5xl flex-col gap-7 px-4 py-8 md:px-6 md:py-10">
      <header className="flex flex-col gap-4 border-b border-border pb-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex flex-col gap-1">
            <button
              type="button"
              onClick={goHome}
              className="w-fit text-3xl font-semibold tracking-tight"
            >
              <span className="text-foreground">Car</span>
              <span className="text-accent">val</span>
            </button>
            <p className="text-[15px] text-muted">{t.header.tagline}</p>
          </div>
          <LocaleSwitcher />
        </div>

        <div
          role="tablist"
          aria-label="Valuation mode"
          className="inline-flex w-fit rounded-lg border border-border bg-surface p-1"
        >
          <ModeTab active={mode === "single"} onClick={() => setMode("single")}>
            {t.tabs.single}
          </ModeTab>
          <ModeTab active={mode === "inventory"} onClick={() => setMode("inventory")}>
            {t.tabs.inventory}
          </ModeTab>
        </div>
      </header>

      {mode === "single" ? <SingleCarModule key={resetKey} /> : <InventoryModule />}
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
