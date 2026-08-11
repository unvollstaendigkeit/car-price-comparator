"use client"

import { useState } from "react"
import type { InventoryReport, InventoryRow, InventoryRowStatus } from "@/lib/types"
import { cn, fmtEur, fmtKm, fmtYear } from "@/lib/format"

type Filter = "all" | InventoryRowStatus

const STATUS_META: Record<InventoryRowStatus, { label: string; cls: string }> = {
  ready: { label: "Ready", cls: "bg-positive-soft/50 text-positive" },
  sold: { label: "Sold", cls: "bg-surface-2 text-faint" },
  review: { label: "Needs review", cls: "bg-negative-soft/50 text-caution" },
}

function StatusPill({ status }: { status: InventoryRowStatus }) {
  const m = STATUS_META[status]
  return (
    <span className={cn("inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium", m.cls)}>
      {m.label}
    </span>
  )
}

/** Dealer inventory review table. Display-only — no scraping is triggered. */
export function InventoryReviewTable({ report }: { report: InventoryReport }) {
  const [filter, setFilter] = useState<Filter>("all")
  const { rows, counts } = report

  const problems = counts.review
  const tabs: { key: Filter; label: string; n: number }[] = [
    { key: "all", label: "All", n: counts.total_rows },
    { key: "ready", label: "Ready", n: counts.valid_for_comparison },
    { key: "review", label: "Needs review", n: counts.review },
    { key: "sold", label: "Sold", n: counts.sold_or_unavailable },
  ]

  const visible = filter === "all" ? rows : rows.filter((r) => r.status_label === filter)

  return (
    <div className="flex flex-col gap-3">
      {/* filter tabs — only show the ones that have rows, plus All */}
      <div className="flex flex-wrap items-center gap-1.5">
        {tabs
          .filter((t) => t.key === "all" || t.n > 0)
          .map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setFilter(t.key)}
              aria-pressed={filter === t.key}
              className={cn(
                "rounded-md border px-2.5 py-1 text-[13px] font-medium transition-colors",
                filter === t.key
                  ? "border-border-strong bg-surface-2 text-foreground"
                  : "border-border text-muted hover:text-foreground",
              )}
            >
              {t.label} <span className="tabular-nums text-faint">{t.n}</span>
            </button>
          ))}
      </div>

      {problems > 0 && (
        <p className="flex items-start gap-1.5 text-[13px] text-caution">
          <span aria-hidden>⚠</span>
          <span>
            {problems} {problems === 1 ? "row needs" : "rows need"} attention before valuation. They&apos;re kept in the
            list, not discarded — fix them in your spreadsheet and re-upload, or continue without them.
          </span>
        </p>
      )}

      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-border bg-surface-2 text-left text-faint">
              <Th className="w-10 text-right">#</Th>
              <Th>Brand</Th>
              <Th>Model</Th>
              <Th>Variant</Th>
              <Th className="text-right">Year</Th>
              <Th>Fuel</Th>
              <Th className="text-right">KM</Th>
              <Th className="text-right">Price</Th>
              <Th>Status</Th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <Row key={r.row_index} r={r} />
            ))}
          </tbody>
        </table>
      </div>

      {visible.length === 0 && (
        <p className="py-4 text-center text-[13px] text-faint">No rows in this category.</p>
      )}
    </div>
  )
}

function Row({ r }: { r: InventoryRow }) {
  const attention = r.status_label === "review"
  const cell = (v: string | null | undefined, extra?: string) => (
    <Td className={extra}>{v && v !== "—" ? v : <span className="text-faint">—</span>}</Td>
  )
  return (
    <tr className={cn("border-b border-border/60 last:border-0 align-top", attention && "bg-negative-soft/10")}>
      <Td className="text-right tabular-nums text-faint">{r.row_number}</Td>
      {cell(r.brand, "font-medium text-foreground")}
      {cell(r.model, "text-foreground")}
      <Td className="max-w-[220px] truncate text-muted" title={r.variant ?? undefined}>
        {r.variant ? r.variant : <span className="text-faint">—</span>}
      </Td>
      {cell(fmtYear(r.year), "text-right tabular-nums")}
      {cell(r.fuel, "")}
      {cell(r.km != null ? fmtKm(r.km) : null, "text-right tabular-nums")}
      <Td className="text-right tabular-nums">
        {r.price != null ? (
          fmtEur(r.price)
        ) : r.status_label === "sold" ? (
          <span className="text-faint">{r.price_original || "—"}</span>
        ) : (
          <span className="text-caution">missing</span>
        )}
      </Td>
      <Td>
        <div className="flex flex-col gap-1">
          <StatusPill status={r.status_label} />
          {attention && r.issues.length > 0 && (
            <span className="text-[11px] leading-tight text-caution">{r.issues.join("; ")}</span>
          )}
        </div>
      </Td>
    </tr>
  )
}

function Th({ children, className }: { children: React.ReactNode; className?: string }) {
  return <th className={cn("px-3 py-2 font-medium", className)}>{children}</th>
}

function Td({ children, className, title }: { children: React.ReactNode; className?: string; title?: string }) {
  return (
    <td className={cn("px-3 py-2", className)} title={title}>
      {children}
    </td>
  )
}
