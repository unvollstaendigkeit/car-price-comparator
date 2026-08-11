"use client"

import { useRef, useState } from "react"
import type { InventoryReport } from "@/lib/types"
import { validateInventory } from "@/lib/api"
import { fmtEur, fmtKm, fmtYear } from "@/lib/format"

export function InventoryView() {
  const [report, setReport] = useState<InventoryReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filename, setFilename] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  async function handleFile(file: File) {
    setBusy(true)
    setError(null)
    setFilename(file.name)
    try {
      const r = await validateInventory(file)
      if (!r.ok) setError(r.error ?? "Validation failed")
      setReport(r.ok ? r : null)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed")
      setReport(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Dropzone */}
      <div
        className="rounded-lg border border-dashed border-border-strong bg-surface px-6 py-10 text-center"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault()
          const f = e.dataTransfer.files?.[0]
          if (f) handleFile(f)
        }}
      >
        <p className="text-sm text-foreground">Drop a dealer inventory file here</p>
        <p className="mt-1 text-xs text-faint">CSV or XLSX — any reasonable column layout is auto-detected</p>
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
          className="mt-4 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground disabled:opacity-40"
        >
          {busy ? "Validating…" : "Choose file"}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xlsx,.xls"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) handleFile(f)
          }}
        />
        {filename && <p className="mt-3 font-mono text-xs text-muted">{filename}</p>}
      </div>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-danger-soft/30 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      {report && report.ok && <InventoryReportView report={report} />}
    </div>
  )
}

function InventoryReportView({ report }: { report: InventoryReport }) {
  const c = report.counts
  const stats = [
    { label: "Total rows", value: c.total_rows, tone: "text-foreground" },
    { label: "Ready to compare", value: c.valid_for_comparison, tone: "text-positive" },
    { label: "Sold / unavailable", value: c.sold_or_unavailable, tone: "text-caution" },
    { label: "Invalid", value: c.invalid, tone: "text-danger" },
  ]

  return (
    <div className="flex flex-col gap-4">
      {/* Counts */}
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-4">
        {stats.map((s) => (
          <div key={s.label} className="bg-surface px-4 py-3">
            <p className="text-[11px] uppercase tracking-wide text-faint">{s.label}</p>
            <p className={`mt-1 font-mono text-2xl tabular-nums ${s.tone}`}>{s.value}</p>
          </div>
        ))}
      </div>

      {/* Column mapping */}
      <div className="rounded-lg border border-border bg-surface">
        <h3 className="border-b border-border px-4 py-2.5 text-sm font-semibold text-foreground">
          Detected column mapping
        </h3>
        <div className="flex flex-wrap gap-2 p-4">
          {Object.entries(report.mapping).map(([src, canonical]) => (
            <span
              key={src}
              className="inline-flex items-center gap-1.5 rounded border border-border bg-background px-2 py-1 text-xs"
            >
              <span className="font-mono text-muted">{src}</span>
              <span className="text-faint">→</span>
              <span className="font-mono text-accent">{canonical}</span>
            </span>
          ))}
        </div>
        {(report.unmapped_columns.length > 0 || report.ambiguities.length > 0) && (
          <div className="flex flex-col gap-2 border-t border-border px-4 py-3 text-xs">
            {report.unmapped_columns.length > 0 && (
              <p className="text-faint">
                Unmapped (ignored): <span className="font-mono text-muted">{report.unmapped_columns.join(", ")}</span>
              </p>
            )}
            {report.ambiguities.map((a, i) => (
              <p key={i} className="text-caution">
                {a}
              </p>
            ))}
          </div>
        )}
      </div>

      {/* Normalized rows */}
      <div className="overflow-hidden rounded-lg border border-border bg-surface">
        <h3 className="border-b border-border px-4 py-2.5 text-sm font-semibold text-foreground">
          Normalized inventory
          <span className="ml-2 text-xs font-normal text-faint">{report.rows.length} rows</span>
        </h3>
        <div className="max-h-[28rem] overflow-auto">
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 bg-surface-2">
              <tr className="text-left text-[11px] uppercase tracking-wide text-faint">
                <th className="px-3 py-2 font-medium">#</th>
                <th className="px-3 py-2 font-medium">Brand</th>
                <th className="px-3 py-2 font-medium">Model</th>
                <th className="px-3 py-2 font-medium">Year</th>
                <th className="px-3 py-2 font-medium">Fuel</th>
                <th className="px-3 py-2 font-medium">Mileage</th>
                <th className="px-3 py-2 font-medium">Price</th>
                <th className="px-3 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {report.rows.map((r) => {
                const excluded = !r.valid_for_comparison
                return (
                  <tr
                    key={r.row_index}
                    className={`border-b border-border/50 last:border-0 ${excluded ? "opacity-55" : ""}`}
                    title={r.issues || undefined}
                  >
                    <td className="px-3 py-1.5 font-mono text-faint">{r.row_index}</td>
                    <td className="px-3 py-1.5 text-foreground">{r.brand ?? "—"}</td>
                    <td className="px-3 py-1.5 text-foreground">{r.model ?? "—"}</td>
                    <td className="px-3 py-1.5 font-mono tabular-nums text-muted">{fmtYear(r.year)}</td>
                    <td className="px-3 py-1.5 text-muted">{r.fuel ?? "—"}</td>
                    <td className="px-3 py-1.5 font-mono tabular-nums text-muted">{fmtKm(r.km)}</td>
                    <td className="px-3 py-1.5 font-mono tabular-nums text-foreground">
                      {r.price !== null ? fmtEur(r.price) : <span className="text-caution">{r.price_original ?? "—"}</span>}
                    </td>
                    <td className="px-3 py-1.5">
                      <StatusTag status={r.status} valid={r.valid_for_comparison} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function StatusTag({ status, valid }: { status: string | null; valid: boolean }) {
  if (status && status !== "active") {
    return (
      <span className="rounded border border-caution/40 bg-caution/10 px-1.5 py-0.5 text-[11px] uppercase tracking-wide text-caution">
        {status}
      </span>
    )
  }
  if (!valid) {
    return (
      <span className="rounded border border-danger/40 bg-danger-soft/30 px-1.5 py-0.5 text-[11px] uppercase tracking-wide text-danger">
        invalid
      </span>
    )
  }
  return (
    <span className="rounded border border-positive/40 bg-positive-soft/30 px-1.5 py-0.5 text-[11px] uppercase tracking-wide text-positive">
      ready
    </span>
  )
}
