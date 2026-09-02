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
  const t = useT()

  return (
    <main className="mx-auto flex min-h-dvh max-w-5xl flex-col gap-7 px-4 py-8 md:px-6 md:py-10">
      <header className="flex flex-col gap-6 border-b border-border pb-8">
        <div className="flex flex-wrap items-start justify-between gap-3">
          {/* Plain <a>, not next/link's <Link>: we're already on "/", and
              Link's client-side router treats a same-path navigation as a
              no-op (no reload, no state reset). A real anchor forces an
              actual reload, which is what "go home" should feel like here
              - locale survives it fine since it lives in localStorage. */}
          <a href="/" className="w-fit text-3xl font-semibold tracking-tight">
            <span className="text-foreground">Car</span>
            <span className="text-accent">val</span>
          </a>
          <LocaleSwitcher />
        </div>

        <p className="-mt-4 text-[16px] text-muted">{t.header.tagline}</p>

        <div
          role="tablist"
          aria-label="Valuation mode"
          className="-mt-4 flex justify-end"
        >
          <div className="inline-flex w-fit rounded-lg border border-border bg-surface p-1">
            <ModeTab active={mode === "single"} onClick={() => setMode("single")}>
              {t.tabs.single}
            </ModeTab>
            <ModeTab active={mode === "inventory"} onClick={() => setMode("inventory")}>
              {t.tabs.inventory}
            </ModeTab>
          </div>
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
