import { Suspense } from "react"
import Link from "next/link"
import { InventoryResultView } from "@/components/inventory-result-view"

export default function InventoryResultPage() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-5xl flex-col gap-7 px-4 py-8 md:px-6 md:py-10">
      <header className="border-b border-border pb-6">
        <Link href="/" className="w-fit text-3xl font-semibold tracking-tight">
          <span className="text-foreground">Car</span>
          <span className="text-accent">val</span>
        </Link>
      </header>

      <Suspense fallback={<ResultFallback />}>
        <InventoryResultView />
      </Suspense>
    </main>
  )
}

function ResultFallback() {
  return <div className="h-24 animate-pulse rounded-lg border border-border bg-surface" />
}
