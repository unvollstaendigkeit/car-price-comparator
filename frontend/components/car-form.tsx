"use client"

import { useState } from "react"
import type { CarInput, ParsedField, ParsedRow } from "@/lib/types"
import { parseRow } from "@/lib/api"

const FUELS = ["", "Petrol", "Diesel", "Hybrid", "PHEV", "Electric", "LPG", "CNG"]
const TRANSMISSIONS = ["", "Manual", "Automatic"]

const EXAMPLES: CarInput[] = [
  { brand: "Toyota", model: "RAV4", variant: "2.5 HSD Executive", year: 2021, fuel: "Hybrid", km: 60000, price: 32000 },
  { brand: "Skoda", model: "Octavia", variant: "2.0 TDI", year: 2019, fuel: "Diesel", km: 120000, price: 15500 },
  { brand: "BMW", model: "3 Series", variant: "320d", year: 2018, fuel: "Diesel", km: 145000, price: 21000 },
]

const PASTE_EXAMPLE =
  "10.7.2014\tDiesel\tVW\tPolo\t1,4 TDI BMT Highline 90HK 5d\thatchback\tWVWZZZ6RZFY064440\tBlack\t280,000\t2,300"

type DetectMap = Partial<Record<keyof CarInput, ParsedField>>

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
  const [mode, setMode] = useState<"manual" | "paste">("manual")
  const [form, setForm] = useState<CarInput>(empty)

  // paste-row state
  const [pasteText, setPasteText] = useState("")
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)
  const [detected, setDetected] = useState<DetectMap>({})
  const [extras, setExtras] = useState<ParsedRow["extras"]>({})
  const [issues, setIssues] = useState<string[]>([])

  const set = <K extends keyof CarInput>(key: K, value: CarInput[K]) => {
    setForm((f) => ({ ...f, [key]: value }))
    // once a field is touched by hand it is no longer a "detected" value
    setDetected((d) => {
      if (!d[key]) return d
      const next = { ...d }
      delete next[key]
      return next
    })
  }

  const num = (v: string): number | undefined => {
    if (v.trim() === "") return undefined
    const n = Number(v.replace(/[^\d.]/g, ""))
    return Number.isFinite(n) ? n : undefined
  }

  const canSubmit = form.brand.trim() !== "" && form.model.trim() !== "" && !busy

  async function handleDetect() {
    if (pasteText.trim() === "" || parsing) return
    setParsing(true)
    setParseError(null)
    try {
      const parsed = await parseRow(pasteText)
      if (!parsed.ok || parsed.mode === "empty") {
        setParseError("Couldn't read any vehicle fields from that text. Check the row and try again.")
        setParsing(false)
        return
      }
      setForm({ ...empty, ...parsed.car })
      setDetected(parsed.fields as DetectMap)
      setExtras(parsed.extras ?? {})
      setIssues(parsed.issues ?? [])
    } catch (err) {
      setParseError(err instanceof Error ? err.message : "Parsing failed")
    } finally {
      setParsing(false)
    }
  }

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

  const anyDetected = Object.keys(detected).length > 0
  const extraNotes = [
    extras.vin ? `VIN ${extras.vin}` : null,
    extras.colour ? `Colour ${extras.colour}` : null,
    ...(extras.notes ?? []),
  ].filter(Boolean) as string[]

  return (
    <form onSubmit={handleSubmit} className="rounded-lg border border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-foreground">Vehicle details</h2>
        <div className="inline-flex rounded-md border border-border p-0.5">
          <ModeTab active={mode === "manual"} onClick={() => setMode("manual")}>
            Manual entry
          </ModeTab>
          <ModeTab active={mode === "paste"} onClick={() => setMode("paste")}>
            Paste a row
          </ModeTab>
        </div>
      </div>

      {mode === "manual" ? (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-4 py-2.5">
          <span className="mr-1 text-[11px] text-faint">Quick fill:</span>
          {EXAMPLES.map((ex) => (
            <button
              key={`${ex.brand}-${ex.model}`}
              type="button"
              onClick={() => {
                setForm({ ...empty, ...ex })
                setDetected({})
              }}
              className="rounded border border-border px-2 py-1 text-[11px] text-muted hover:border-border-strong hover:text-foreground"
            >
              {ex.brand} {ex.model}
            </button>
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-2 border-b border-border px-4 py-3">
          <label className="text-xs font-medium text-muted" htmlFor="paste-row">
            Paste one row from a spreadsheet, listing, or table
          </label>
          <textarea
            id="paste-row"
            className="ab-input min-h-[64px] resize-y font-mono text-xs leading-relaxed"
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
            placeholder={"e.g.  " + PASTE_EXAMPLE.replace(/\t/g, "  |  ")}
          />
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={handleDetect}
              disabled={pasteText.trim() === "" || parsing}
              className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-accent-foreground transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
            >
              {parsing ? "Detecting…" : "Detect fields"}
            </button>
            <button
              type="button"
              onClick={() => setPasteText(PASTE_EXAMPLE)}
              className="rounded border border-border px-2 py-1 text-[11px] text-muted hover:border-border-strong hover:text-foreground"
            >
              Try an example
            </button>
            <span className="text-[11px] text-faint">
              Tab, comma, semicolon, pipe, or Markdown-table rows all work. Header rows are auto-detected.
            </span>
          </div>
          {parseError && <p className="text-xs text-danger">{parseError}</p>}
          {anyDetected && (
            <div className="flex flex-col gap-1 rounded-md border border-border bg-surface-2 px-3 py-2">
              <p className="text-[11px] text-muted">
                Detected fields are filled below — review and edit anything before comparing.{" "}
                <span className="text-faint">Amber marks a low-confidence guess.</span>
              </p>
              {extraNotes.length > 0 && (
                <p className="text-[11px] text-faint">Also found (not used for matching): {extraNotes.join(" · ")}</p>
              )}
              {issues.length > 0 && <p className="text-[11px] text-caution">{issues.join(" · ")}</p>}
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 p-4 md:grid-cols-3">
        <Field label="Brand" required mark={detected.brand}>
          <input
            className="ab-input"
            value={form.brand}
            onChange={(e) => set("brand", e.target.value)}
            placeholder="Skoda"
          />
        </Field>
        <Field label="Model" required mark={detected.model}>
          <input
            className="ab-input"
            value={form.model}
            onChange={(e) => set("model", e.target.value)}
            placeholder="Octavia"
          />
        </Field>
        <Field label="Variant / engine" hint="e.g. 2.0 TDI 150HP" mark={detected.variant}>
          <input
            className="ab-input"
            value={form.variant ?? ""}
            onChange={(e) => set("variant", e.target.value)}
            placeholder="2.0 TDI"
          />
        </Field>

        <Field label="Year" mark={detected.year}>
          <input
            className="ab-input"
            inputMode="numeric"
            value={form.year ?? ""}
            onChange={(e) => set("year", num(e.target.value))}
            placeholder="2019"
          />
        </Field>
        <Field label="Fuel" mark={detected.fuel}>
          <select className="ab-input" value={form.fuel ?? ""} onChange={(e) => set("fuel", e.target.value)}>
            {FUELS.map((f) => (
              <option key={f} value={f}>
                {f === "" ? "—" : f}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Mileage (km)" mark={detected.km}>
          <input
            className="ab-input"
            inputMode="numeric"
            value={form.km ?? ""}
            onChange={(e) => set("km", num(e.target.value))}
            placeholder="120000"
          />
        </Field>

        <Field label="Asking price (€)" mark={detected.price}>
          <input
            className="ab-input"
            inputMode="numeric"
            value={form.price ?? ""}
            onChange={(e) => set("price", num(e.target.value))}
            placeholder="15500"
          />
        </Field>
        <Field label="Power (kW)" mark={detected.power_kw}>
          <input
            className="ab-input"
            inputMode="numeric"
            value={form.power_kw ?? ""}
            onChange={(e) => set("power_kw", num(e.target.value))}
            placeholder="110"
          />
        </Field>
        <Field label="Transmission" mark={detected.transmission}>
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
      onClick={onClick}
      aria-pressed={active}
      className={
        "rounded px-3 py-1 text-xs font-medium transition-colors " +
        (active ? "bg-accent text-accent-foreground" : "text-muted hover:text-foreground")
      }
    >
      {children}
    </button>
  )
}

function Field({
  label,
  required,
  hint,
  mark,
  children,
}: {
  label: string
  required?: boolean
  hint?: string
  mark?: ParsedField
  children: React.ReactNode
}) {
  const lowConf = mark?.detected && (mark.confidence === "low" || mark.confidence === "medium")
  return (
    <label className="flex flex-col gap-1">
      <span className="flex items-center gap-1 text-xs font-medium text-muted">
        {label}
        {required && <span className="text-accent">*</span>}
        {mark?.detected && (
          <span
            className={"ml-auto h-1.5 w-1.5 rounded-full " + (lowConf ? "bg-caution" : "bg-accent")}
            aria-hidden="true"
          />
        )}
      </span>
      {children}
      {hint && !lowConf && <span className="text-[11px] text-faint">{hint}</span>}
      {lowConf && <span className="text-[11px] text-caution">Low-confidence guess — please verify</span>}
    </label>
  )
}
