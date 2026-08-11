"use client"

import { useState } from "react"
import type { CarInput } from "@/lib/types"

const FUELS = ["", "Petrol", "Diesel", "Hybrid", "PHEV", "Electric", "LPG", "CNG"]
const TRANSMISSIONS = ["", "Manual", "Automatic"]

const EXAMPLES: CarInput[] = [
  { brand: "Toyota", model: "RAV4", variant: "2.5 HSD Executive", year: 2021, fuel: "Hybrid", km: 60000, price: 32000 },
  { brand: "Skoda", model: "Octavia", variant: "2.0 TDI", year: 2019, fuel: "Diesel", km: 120000, price: 15500 },
  { brand: "BMW", model: "3 Series", variant: "320d", year: 2018, fuel: "Diesel", km: 145000, price: 21000 },
]

const empty: CarInput = {
  brand: "",
  model: "",
  variant: "",
  year: undefined,
  fuel: "",
  km: undefined,
  price: undefined,
  power_kw: undefined,
  transmission: "",
  body_type: "",
}

export function CarForm({
  onSubmit,
  busy,
}: {
  onSubmit: (input: CarInput) => void
  busy: boolean
}) {
  const [form, setForm] = useState<CarInput>(empty)

  const set = <K extends keyof CarInput>(key: K, value: CarInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const num = (v: string): number | undefined => {
    if (v.trim() === "") return undefined
    const n = Number(v.replace(/[^\d.]/g, ""))
    return Number.isFinite(n) ? n : undefined
  }

  const canSubmit = form.brand.trim() !== "" && form.model.trim() !== "" && !busy

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    onSubmit({
      ...form,
      brand: form.brand.trim(),
      model: form.model.trim(),
      variant: form.variant?.trim() || undefined,
      fuel: form.fuel || undefined,
      transmission: form.transmission || undefined,
      body_type: form.body_type?.trim() || undefined,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-foreground">Vehicle details</h2>
        <div className="flex items-center gap-1.5">
          {EXAMPLES.map((ex) => (
            <button
              key={`${ex.brand}-${ex.model}`}
              type="button"
              onClick={() => setForm({ ...empty, ...ex })}
              className="rounded border border-border px-2 py-1 text-[11px] text-muted hover:border-border-strong hover:text-foreground"
            >
              {ex.brand} {ex.model}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 p-4 md:grid-cols-3">
        <Field label="Brand" required>
          <input
            className="ab-input"
            value={form.brand}
            onChange={(e) => set("brand", e.target.value)}
            placeholder="Skoda"
            autoFocus
          />
        </Field>
        <Field label="Model" required>
          <input
            className="ab-input"
            value={form.model}
            onChange={(e) => set("model", e.target.value)}
            placeholder="Octavia"
          />
        </Field>
        <Field label="Variant / engine" hint="e.g. 2.0 TDI 150HP">
          <input
            className="ab-input"
            value={form.variant ?? ""}
            onChange={(e) => set("variant", e.target.value)}
            placeholder="2.0 TDI"
          />
        </Field>

        <Field label="Year">
          <input
            className="ab-input"
            inputMode="numeric"
            value={form.year ?? ""}
            onChange={(e) => set("year", num(e.target.value))}
            placeholder="2019"
          />
        </Field>
        <Field label="Fuel">
          <select className="ab-input" value={form.fuel ?? ""} onChange={(e) => set("fuel", e.target.value)}>
            {FUELS.map((f) => (
              <option key={f} value={f}>
                {f === "" ? "—" : f}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Mileage (km)">
          <input
            className="ab-input"
            inputMode="numeric"
            value={form.km ?? ""}
            onChange={(e) => set("km", num(e.target.value))}
            placeholder="120000"
          />
        </Field>

        <Field label="Asking price (€)">
          <input
            className="ab-input"
            inputMode="numeric"
            value={form.price ?? ""}
            onChange={(e) => set("price", num(e.target.value))}
            placeholder="15500"
          />
        </Field>
        <Field label="Power (kW)">
          <input
            className="ab-input"
            inputMode="numeric"
            value={form.power_kw ?? ""}
            onChange={(e) => set("power_kw", num(e.target.value))}
            placeholder="110"
          />
        </Field>
        <Field label="Transmission">
          <select
            className="ab-input"
            value={form.transmission ?? ""}
            onChange={(e) => set("transmission", e.target.value)}
          >
            {TRANSMISSIONS.map((t) => (
              <option key={t} value={t}>
                {t === "" ? "—" : t}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="flex items-center justify-between gap-4 border-t border-border px-4 py-3">
        <p className="text-xs text-faint">
          Brand and model are required. The more fields you provide, the tighter the comparable match.
        </p>
        <button
          type="submit"
          disabled={!canSubmit}
          className="shrink-0 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? "Comparing…" : "Compare prices"}
        </button>
      </div>
    </form>
  )
}

function Field({
  label,
  required,
  hint,
  children,
}: {
  label: string
  required?: boolean
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="flex items-center gap-1 text-xs font-medium text-muted">
        {label}
        {required && <span className="text-accent">*</span>}
      </span>
      {children}
      {hint && <span className="text-[11px] text-faint">{hint}</span>}
    </label>
  )
}
