"use client"

import type { Comparable } from "@/lib/types"
import { fmtEur, fmtKm, fmtYear } from "@/lib/format"
import { useT } from "@/lib/i18n/use-t"

export function ComparablesTable({ rows }: { rows: Comparable[] }) {
  const t = useT()
  if (!rows || rows.length === 0) {
    return (
      <p className="px-4 py-6 text-center text-sm text-faint">
        {t.table.noComparables}
      </p>
    )
  }

  return (
    <div className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-border text-left text-[13px] uppercase tracking-wide text-faint">
              <th className="px-3 py-2.5 font-medium">{t.table.price}</th>
              <th className="px-3 py-2.5 font-medium">{t.table.year}</th>
              <th className="px-3 py-2.5 font-medium">{t.table.mileage}</th>
              <th className="px-3 py-2.5 font-medium">{t.table.listing}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr
                key={`${r.url ?? "row"}-${i}`}
                className="border-b border-border/50 last:border-0 hover:bg-surface-2/60"
              >
                <td className="px-3 py-2 font-mono tabular-nums text-foreground">{fmtEur(r.price)}</td>
                <td className="px-3 py-2 font-mono tabular-nums text-muted">{fmtYear(r.year)}</td>
                <td className="px-3 py-2 font-mono tabular-nums text-muted">{fmtKm(r.km)}</td>
                <td className="max-w-[22rem] truncate px-3 py-2">
                  {r.url ? (
                    <a
                      href={r.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-accent underline-offset-2 hover:underline"
                      title={r.title ?? r.url}
                    >
                      {r.title ?? r.url}
                    </a>
                  ) : (
                    <span className="text-muted">{r.title ?? "—"}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
