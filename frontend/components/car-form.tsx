"use client"

import { useState } from "react"
import type { CarInput, ParsedField } from "@/lib/types"
import { parseRow } from "@/lib/api"
import { fmtEur, fmtKm, fmtYear, mapLabel } from "@/lib/format"
import { useT } from "@/lib/i18n/use-t"

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
  onClear,
  busy,
}: {
  onSubmit: (input: CarInput) => void
  onClear?: () => void
  busy: boolean
}) {
  const t = useT()
  const [mode, setMode] = useState<"manual" | "paste" | "vin">("paste")
  const [form, setForm] = useState<CarInput>(empty)

  // paste-row state
  const [pasteText, setPasteText] = useState("")
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)
  const [detected, setDetected] = useState<DetectMap>({})
  // VIN detected from a pasted row (backend row_parser extras) — not a form
  // field, just shown in the "Detected vehicle" summary.
  const [detectedVin, setDetectedVin] = useState<string | null>(null)
  // Collapses the paste textarea once a row has been successfully detected,
  // so the form reads as "detected vehicle" first. Re-expandable by hand.
  const [pasteCollapsed, setPasteCollapsed] = useState(false)

  // VIN lookup state — UI only for now, not wired to a backend/external
  // lookup yet.
  const [vin, setVin] = useState("")

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
        setParseError(t.form.paste.parseError)
        setParsing(false)
        return
      }
      setForm({ ...empty, ...parsed.car })
      setDetected(parsed.fields as DetectMap)
      setDetectedVin(parsed.extras?.vin ?? null)
      setPasteCollapsed(true)
    } catch (err) {
      setParseError(err instanceof Error ? err.message : t.form.paste.parseFailed)
    } finally {
      setParsing(false)
    }
  }

  // Reset everything back to the initial input state (paste mode, empty form),
  // and ask the parent to drop any current comparison results. No page reload.
  const handleClear = () => {
    setForm(empty)
    setDetected({})
    setDetectedVin(null)
    setParseError(null)
    setPasteText("")
    setVin("")
    setMode("paste")
    setPasteCollapsed(false)
    onClear?.()
  }

  const hasInput =
    pasteText.trim() !== "" ||
    vin.trim() !== "" ||
    Object.values(form).some((v) => v !== "" && v !== undefined)

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
      vin: detectedVin || undefined,
    })
  }

  const anyDetected = Object.keys(detected).length > 0

  // Human-readable "Detected vehicle" summary line.
  const detectedTitle = [form.brand, form.model, form.variant].filter(Boolean).join(" ")
  const detectedSpec = [
    fmtYear(form.year),
    mapLabel(form.fuel, t.form.fuelLabels),
    form.km ? fmtKm(form.km) : null,
    mapLabel(form.body_type || null, t.form.bodyTypeLabels),
    detectedVin,
    mapLabel(form.transmission || null, t.form.transmissionLabels),
  ]
    .filter((v) => v && v !== "—")
    .join(" · ")

  // Only warn when something genuinely important is missing or uncertain.
  const missingImportant = anyDetected && (!form.brand || !form.model || !form.year || !form.price)

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5 rounded-lg border border-border bg-surface px-5 py-5">
      <div className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-foreground">{t.form.title}</h2>
        <div className="flex gap-5 border-b border-border">
          <ModeTab active={mode === "paste"} onClick={() => setMode("paste")}>
            {t.form.tabs.paste}
          </ModeTab>
          <ModeTab active={mode === "manual"} onClick={() => setMode("manual")}>
            {t.form.tabs.manual}
          </ModeTab>
          <ModeTab active={mode === "vin"} onClick={() => setMode("vin")}>
            {t.form.tabs.vin}
          </ModeTab>
        </div>
      </div>

      {mode === "manual" && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-[13px] text-faint">{t.form.quickFill}</span>
          {EXAMPLES.map((ex) => (
            <button
              key={`${ex.brand}-${ex.model}`}
              type="button"
              onClick={() => {
                setForm({ ...empty, ...ex })
                setDetected({})
                setDetectedVin(null)
              }}
              className="rounded border border-border px-2.5 py-1 text-[13px] text-muted hover:border-border-strong hover:text-foreground"
            >
              {ex.brand} {ex.model}
            </button>
          ))}
        </div>
      )}

      {mode === "paste" && (
        <div className="flex flex-col gap-3">
          {pasteCollapsed && anyDetected ? (
            <button
              type="button"
              onClick={() => setPasteCollapsed(false)}
              className="flex items-center gap-1.5 self-start text-[13px] text-muted underline-offset-2 hover:text-foreground hover:underline"
            >
              <span aria-hidden>✎</span>
              {t.form.paste.editPasted}
            </button>
          ) : (
            <>
              <h3 className="text-[15px] font-medium text-foreground">{t.form.paste.title}</h3>
              <textarea
                id="paste-row"
                className="ab-input min-h-[76px] resize-y font-mono text-[13px] leading-relaxed"
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                placeholder={t.form.paste.placeholder}
              />
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={handleDetect}
                  disabled={pasteText.trim() === "" || parsing}
                  className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {parsing ? t.form.paste.detecting : t.form.paste.detect}
                </button>
                <button
                  type="button"
                  onClick={() => setPasteText(PASTE_EXAMPLE)}
                  className="text-[13px] text-muted underline-offset-2 hover:text-foreground hover:underline"
                >
                  {t.form.paste.tryExample}
                </button>
              </div>
              {parseError && <p className="text-sm text-danger">{parseError}</p>}
            </>
          )}

          {anyDetected && (
            <div className="flex flex-col gap-0.5">
              <p className="text-[14px] text-foreground">
                <span className="text-accent" aria-hidden>
                  ✓
                </span>{" "}
                {t.form.detected.recognizedPrefix}:{" "}
                <span className="font-semibold">{detectedTitle || t.form.detected.fallback}</span>
              </p>
              {detectedSpec && <p className="pl-[21px] text-[13px] text-muted">{detectedSpec}</p>}

              {missingImportant && (
                <p className="mt-0.5 flex items-start gap-1.5 pl-[21px] text-[13px] text-caution">
                  <span aria-hidden>⚠</span>
                  <span>{t.form.detected.missingWarning}</span>
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {mode === "vin" && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <h3 className="text-[15px] font-medium text-foreground">{t.form.vin.title}</h3>
            <p className="text-[13px] text-muted">{t.form.vin.description}</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <input
              className="ab-input max-w-xs font-mono text-[13px] uppercase tracking-wide"
              value={vin}
              onChange={(e) => setVin(e.target.value.toUpperCase())}
              placeholder="WVWZZZ6RZFY064440"
              maxLength={17}
            />
            <button
              type="button"
              disabled
              title={t.form.vin.comingSoonTitle}
              className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground opacity-40 disabled:cursor-not-allowed"
            >
              {t.form.vin.lookup}
            </button>
            <span className="text-[13px] text-faint">{t.form.vin.comingSoon}</span>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
        <Field label={t.form.fields.brand} required mark={detected.brand}>
          <input
            className="ab-input"
            value={form.brand}
            onChange={(e) => set("brand", e.target.value)}
            placeholder="Skoda"
          />
        </Field>
        <Field label={t.form.fields.model} required mark={detected.model}>
          <input
            className="ab-input"
            value={form.model}
            onChange={(e) => set("model", e.target.value)}
            placeholder="Octavia"
          />
        </Field>
        <Field label={t.form.fields.variant} hint={t.form.fields.variantHint} mark={detected.variant}>
          <input
            className="ab-input"
            value={form.variant ?? ""}
            onChange={(e) => set("variant", e.target.value)}
            placeholder="2.0 TDI"
          />
        </Field>

        <Field label={t.form.fields.year} mark={detected.year}>
          <input
            className="ab-input"
            inputMode="numeric"
            value={form.year ?? ""}
            onChange={(e) => set("year", num(e.target.value))}
            placeholder="2019"
          />
        </Field>
        <Field label={t.form.fields.fuel} mark={detected.fuel}>
          <select className="ab-input" value={form.fuel ?? ""} onChange={(e) => set("fuel", e.target.value)}>
            {FUELS.map((f) => (
              <option key={f} value={f}>
                {f === "" ? t.form.none : t.form.fuelLabels[f as keyof typeof t.form.fuelLabels]}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t.form.fields.km} mark={detected.km}>
          <input
            className="ab-input"
            inputMode="numeric"
            value={form.km ?? ""}
            onChange={(e) => set("km", num(e.target.value))}
            placeholder="120000"
          />
        </Field>

        <Field label={t.form.fields.price} mark={detected.price}>
          <input
            className="ab-input"
            inputMode="numeric"
            value={form.price ?? ""}
            onChange={(e) => set("price", num(e.target.value))}
            placeholder="15500"
          />
        </Field>
        <Field label={t.form.fields.power} mark={detected.power_kw}>
          <input
            className="ab-input"
            inputMode="numeric"
            value={form.power_kw ?? ""}
            onChange={(e) => set("power_kw", num(e.target.value))}
            placeholder="110"
          />
        </Field>
        <Field label={t.form.fields.transmission} mark={detected.transmission}>
          <select
            className="ab-input"
            value={form.transmission ?? ""}
            onChange={(e) => set("transmission", e.target.value)}
          >
            {TRANSMISSIONS.map((tr) => (
              <option key={tr} value={tr}>
                {tr === "" ? t.form.none : t.form.transmissionLabels[tr as keyof typeof t.form.transmissionLabels]}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-4">
        <p className="text-[13px] text-faint">{t.form.footerHint}</p>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={handleClear}
            disabled={busy || !hasInput}
            className="rounded-md border border-border px-4 py-2.5 text-sm font-medium text-muted transition-colors hover:border-border-strong hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
          >
            {t.form.clear}
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-md bg-accent px-5 py-2.5 text-sm font-semibold text-accent-foreground transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? t.form.submitting : t.form.submit}
          </button>
        </div>
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
        "-mb-px border-b-2 px-0.5 pb-2 text-[13px] font-medium transition-colors " +
        (active ? "border-accent text-accent" : "border-transparent text-muted hover:text-foreground")
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
  const t = useT()
  const lowConf = mark?.detected && (mark.confidence === "low" || mark.confidence === "medium")
  return (
    <label className="flex flex-col gap-1.5">
      <span className="flex items-center gap-1 text-[13px] font-medium text-muted">
        {label}
        {required && <span className="ml-0.5 text-lg font-bold leading-none text-accent">*</span>}
      </span>
      <span className="relative flex items-center">
        {children}
        {lowConf && (
          <span className="group absolute right-2.5 flex cursor-help items-center" aria-hidden="true">
            <span className="text-[13px] text-caution">⚠</span>
            <span className="pointer-events-none absolute bottom-full right-0 z-10 mb-1.5 w-max max-w-[220px] rounded-md bg-foreground px-2 py-1 text-[12px] font-normal leading-snug text-background opacity-0 shadow-md transition-opacity duration-150 group-hover:opacity-100">
              {t.form.verifyTooltip}
            </span>
          </span>
        )}
      </span>
      {hint && <span className="text-[13px] text-faint">{hint}</span>}
    </label>
  )
}
