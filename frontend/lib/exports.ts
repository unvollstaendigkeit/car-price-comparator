/**
 * Results-sharing / export layer (presentation ONLY).
 *
 * This module never recomputes valuations — it reflects the exact values the
 * app already displays (see lib/types.ts). Autobazar.eu and Bazoš.sk are kept
 * strictly separate; no blended market price is ever produced. No persistence,
 * no network, no result IDs — everything is generated client-side on demand.
 *
 * Sign conventions (mirrored from the backend, never re-derived):
 *   • undervaluation_pct / price_diff_pct = (median − asking) / asking × 100
 *       positive  → priced BELOW market (a potential find)
 *       negative  → priced ABOVE market
 *   • price difference € = median − asking   (positive → below market)
 *     For inventory (which carries no € field) we derive it from the two values
 *     already shown — median and asking — using that same definition.
 */

import type {
  AnalysisCarResult,
  AnalysisSourceResult,
  AnalysisSummary,
  CompareResult,
  Comparable,
  ConfidenceFlag,
  MileageMatch,
  SourceResult,
} from "./types"
import { buildXlsxBlob, type CellValue } from "./xlsx"

/* -------------------------------------------------------------------------- */
/* Shared label helpers (wording mirrors the on-screen badges)                */
/* -------------------------------------------------------------------------- */
const CONFIDENCE_LABEL: Record<ConfidenceFlag, string> = {
  HIGH: "High confidence",
  MEDIUM: "Medium confidence",
  LOW: "Low confidence",
  INSUFFICIENT: "Insufficient data",
}

const MILEAGE_LABEL: Record<MileageMatch, string> = {
  good: "Matches comparables",
  moderate: "Differs somewhat",
  large: "Differs materially",
  very_large: "Mismatch",
  unknown: "Not verified",
}

const MILEAGE_SEVERITY: Record<MileageMatch, number> = {
  very_large: 4,
  large: 3,
  moderate: 2,
  unknown: 1,
  good: 0,
}

const TIER_LABEL: Record<string, string> = {
  strict: "Strict match",
  relaxed: "Relaxed",
  broad: "Broad",
}

function tierLabel(tier: string | null | undefined): string {
  if (!tier) return ""
  return TIER_LABEL[tier.toLowerCase()] ?? tier.charAt(0).toUpperCase() + tier.slice(1).toLowerCase()
}

/** Direction word for a price-difference %, matching the DiffBadge thresholds. */
function diffWord(pct: number | null | undefined): "below market" | "above market" | "at market" | "" {
  if (pct === null || pct === undefined || Number.isNaN(pct)) return ""
  if (pct >= 2) return "below market"
  if (pct <= -2) return "above market"
  return "at market"
}

/* -------------------------------------------------------------------------- */
/* Number / text formatting (plain, no JSX — safe for HTML & xlsx)            */
/* -------------------------------------------------------------------------- */
function round(v: number | null | undefined): number | null {
  if (v === null || v === undefined || Number.isNaN(v)) return null
  return Math.round(v)
}

function eur(v: number | null | undefined): string {
  const r = round(v)
  return r === null ? "—" : `€${r.toLocaleString("en-US")}`
}

function signedEur(v: number | null | undefined): string {
  const r = round(v)
  if (r === null) return "—"
  const sign = r > 0 ? "+" : r < 0 ? "−" : ""
  return `${sign}€${Math.abs(r).toLocaleString("en-US")}`
}

function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—"
  const sign = v > 0 ? "+" : v < 0 ? "−" : ""
  return `${sign}${Math.abs(v).toFixed(digits)}%`
}

function km(v: number | null | undefined): string {
  const r = round(v)
  return r === null ? "—" : `${r.toLocaleString("en-US")} km`
}

function yr(v: number | null | undefined): string {
  const r = round(v)
  return r === null ? "—" : String(r)
}

function esc(s: unknown): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
}

function slug(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "")
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

/** € difference between market median and asking, per the backend definition. */
function diffEur(median: number | null, asking: number | null | undefined): number | null {
  if (median === null || asking === null || asking === undefined) return null
  return median - asking
}

function toneClass(pctVal: number | null | undefined): "pos" | "neg" | "neutral" {
  if (pctVal === null || pctVal === undefined || Number.isNaN(pctVal)) return "neutral"
  if (pctVal >= 2) return "pos"
  if (pctVal <= -2) return "neg"
  return "neutral"
}

/* -------------------------------------------------------------------------- */
/* Browser triggers                                                           */
/* -------------------------------------------------------------------------- */
function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}

function openHtmlInNewTab(html: string) {
  const w = window.open("", "_blank")
  if (w) {
    w.document.open()
    w.document.write(html)
    w.document.close()
    return
  }
  // Popup blocked → fall back to a blob URL the browser can still open.
  const blob = new Blob([html], { type: "text/html;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  window.open(url, "_blank")
  setTimeout(() => URL.revokeObjectURL(url), 15000)
}

/* ========================================================================== */
/* 1 — SINGLE-CAR: downloadable, print-friendly HTML report                   */
/* ========================================================================== */
function comparablesRows(list: Comparable[]): string {
  const rows = list
    .filter((c) => c.price !== null || c.year !== null || c.km !== null || c.title || c.url)
    .map((c) => {
      const title = c.title ? esc(c.title) : "—"
      const link = c.url
        ? `<a href="${esc(c.url)}" target="_blank" rel="noreferrer">View listing</a>`
        : "—"
      return `<tr>
        <td class="num">${eur(c.price)}</td>
        <td class="num">${yr(c.year)}</td>
        <td class="num">${km(c.km)}</td>
        <td>${title}</td>
        <td class="link">${link}</td>
      </tr>`
    })
    .join("")
  if (!rows) return `<p class="empty">No comparable listings captured.</p>`
  return `<table class="comps">
    <thead><tr><th class="num">Price</th><th class="num">Year</th><th class="num">Mileage</th><th>Title</th><th>Link</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`
}

function sourceSection(title: string, host: string, s: SourceResult, asking: number | null): string {
  if (s.retrieval_error) {
    return `<section class="src">
      <div class="src-head"><h3>${esc(title)}</h3><span class="host">${esc(host)}</span></div>
      <p class="warn">Could not retrieve data: ${esc(s.retrieval_error)}</p>
    </section>`
  }
  if (s.insufficient || s.comparable_count === 0 || s.undervaluation_pct === null) {
    return `<section class="src">
      <div class="src-head"><h3>${esc(title)}</h3><span class="host">${esc(host)}</span></div>
      <p class="warn">Insufficient comparable listings for a reliable estimate (${s.comparable_count} found).
      ${esc(s.sample_warning || "")}</p>
      ${comparablesRows(s.comparables)}
    </section>`
  }
  const tone = toneClass(s.undervaluation_pct)
  const dEur = s.price_difference_eur
  const mileage = MILEAGE_LABEL[s.mileage_match]
  const mileageSerious = s.mileage_match === "large" || s.mileage_match === "very_large"
  return `<section class="src">
    <div class="src-head">
      <h3>${esc(title)}</h3>
      <span class="host">${esc(host)}</span>
      <span class="spacer"></span>
      ${s.tier ? `<span class="pill">${esc(tierLabel(s.tier))}</span>` : ""}
      <span class="pill">n=${s.comparable_count}</span>
    </div>
    <div class="metrics">
      <div class="metric"><span class="k">Market median</span><span class="v num">${eur(s.median_asking_eur)}</span></div>
      <div class="metric"><span class="k">P25 – P75</span><span class="v num">${eur(s.market_p25_eur)} – ${eur(s.market_p75_eur)}</span></div>
      <div class="metric"><span class="k">Difference vs asking</span><span class="v num ${tone}">${signedEur(dEur)} · ${pct(s.undervaluation_pct)}</span></div>
      <div class="metric"><span class="k">Assessment</span><span class="v ${tone}">${esc(diffWord(s.undervaluation_pct))}</span></div>
    </div>
    <div class="mileage ${mileageSerious ? "warn-box" : ""}">
      <strong>Mileage similarity:</strong> ${esc(mileage)}
      ${
        s.mileage.comp_km_median != null
          ? ` · comparables ${km(s.mileage.comp_km_p25)}–${km(s.mileage.comp_km_p75)} (median ${km(
              s.mileage.comp_km_median,
            )}), this car ${km(s.mileage.submitted_km)}`
          : ""
      }
    </div>
    ${comparablesRows(s.comparables)}
  </section>`
}

function singleCarReportHtml(result: CompareResult): string {
  const { car, sources, cross_source, confidence } = result
  const heading = `${car.brand} ${car.model}`
  const specLine = [yr(car.year), car.fuel, km(car.km), car.transmission, car.body_type]
    .filter((x) => x && x !== "—")
    .map(esc)
    .join(" · ")

  const powerLine =
    car.power_kw != null ? `${car.power_kw} kW${car.power_source ? ` (${esc(car.power_source)})` : ""}` : "—"

  const agreementCopy: Record<string, string> = {
    agree: "Sources agree — both marketplaces produced closely aligned medians.",
    meaningful: "Limited agreement — the marketplaces differ enough to treat with care.",
    large: "Sources disagree — inspect the comparables on each before trusting either.",
  }
  const agreement = cross_source.agreement ? agreementCopy[cross_source.agreement] : ""

  const details: [string, string][] = [
    ["Brand", esc(car.brand)],
    ["Model", esc(car.model)],
    ["Variant", esc(car.variant || car.variant_engine || "—")],
    ["Year", yr(car.year)],
    ["Fuel", esc(car.fuel || "—")],
    ["Mileage", km(car.km)],
    ["Asking price", eur(car.asking_price_eur)],
    ["Power", powerLine],
    ["Transmission", esc(car.transmission || "—")],
    ["Body type", esc(car.body_type || "—")],
  ]

  const warnings = confidence.warnings.length
    ? `<ul class="warnings">${confidence.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`
    : ""

  return `<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Carval report — ${esc(heading)}</title>
<style>
  :root{--fg:#0f1720;--muted:#5b6673;--faint:#8a94a0;--line:#e4e8ec;--soft:#f6f8fa;
    --accent:#0e97a7;--pos:#0f9d6b;--neg:#d1662a;--caution:#b0820c;}
  *{box-sizing:border-box}
  body{margin:0;background:#fff;color:var(--fg);
    font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased;}
  .wrap{max-width:820px;margin:0 auto;padding:44px 40px 72px;}
  .num{font-variant-numeric:tabular-nums;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;}
  .pos{color:var(--pos)}.neg{color:var(--neg)}.neutral{color:var(--muted)}
  .topbar{display:flex;justify-content:space-between;align-items:baseline;border-bottom:2px solid var(--fg);padding-bottom:12px;margin-bottom:8px;}
  .brand{font-size:22px;font-weight:600;letter-spacing:-0.01em}
  .brand span{color:var(--accent)}
  .doc-meta{font-size:12px;color:var(--faint)}
  h1{font-size:26px;margin:22px 0 2px;letter-spacing:-0.01em}
  .spec{color:var(--muted);margin:0 0 4px}
  h2{font-size:12px;text-transform:uppercase;letter-spacing:0.08em;color:var(--faint);
    border-bottom:1px solid var(--line);padding-bottom:6px;margin:30px 0 14px;}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:6px 28px}
  .dl{display:flex;justify-content:space-between;gap:12px;border-bottom:1px dotted var(--line);padding:5px 0}
  .dl .k{color:var(--muted)}.dl .v{font-weight:500}
  .conf{display:inline-flex;align-items:center;gap:8px;font-weight:600;padding:5px 12px;border-radius:999px;border:1px solid var(--line);}
  .conf.HIGH{color:var(--pos);border-color:var(--pos)}
  .conf.MEDIUM{color:var(--accent);border-color:var(--accent)}
  .conf.LOW{color:var(--caution);border-color:var(--caution)}
  .conf.INSUFFICIENT{color:var(--faint)}
  .reasons{color:var(--muted);margin:12px 0 0}
  .warnings{margin:10px 0 0;padding-left:18px;color:var(--caution)}
  .agreement{margin-top:10px;color:var(--muted)}
  .src{border:1px solid var(--line);border-radius:10px;padding:18px 18px 8px;margin-bottom:18px}
  .src-head{display:flex;align-items:center;gap:10px;margin-bottom:12px}
  .src-head h3{margin:0;font-size:16px}
  .host{color:var(--faint);font-size:12px}
  .spacer{flex:1}
  .pill{font-size:11px;font-family:ui-monospace,monospace;text-transform:uppercase;letter-spacing:0.04em;
    border:1px solid var(--line);border-radius:5px;padding:2px 6px;color:var(--muted)}
  .metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px 24px;margin-bottom:12px}
  .metric{display:flex;flex-direction:column;gap:1px}
  .metric .k{font-size:12px;color:var(--faint)}.metric .v{font-weight:600}
  .mileage{font-size:13px;color:var(--muted);padding:8px 0;border-top:1px solid var(--line)}
  .warn-box{color:var(--neg)}
  .warn{color:var(--neg);margin:4px 0 10px}
  table.comps{width:100%;border-collapse:collapse;margin:8px 0 12px;font-size:13px}
  table.comps th{text-align:left;color:var(--faint);font-weight:500;border-bottom:1px solid var(--line);padding:6px 8px}
  table.comps td{border-bottom:1px solid var(--soft);padding:6px 8px}
  table.comps th.num,table.comps td.num{text-align:right}
  table.comps td.link a{color:var(--accent);text-decoration:none}
  .empty{color:var(--faint);font-size:13px;margin:4px 0 12px}
  .print-btn{position:fixed;top:16px;right:16px;background:var(--accent);color:#fff;border:0;
    border-radius:8px;padding:9px 16px;font-size:13px;font-weight:600;cursor:pointer}
  .foot{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);color:var(--faint);font-size:12px}
  @media print{.print-btn{display:none}.wrap{padding:0}body{font-size:12px}}
</style></head>
<body>
  <button class="print-btn" onclick="window.print()">Print / Save as PDF</button>
  <div class="wrap">
    <div class="topbar">
      <div class="brand">Car<span>val</span></div>
      <div class="doc-meta">Valuation report · generated ${esc(todayISO())}</div>
    </div>

    <h1>${esc(heading)}${car.variant_engine ? ` <span class="num" style="font-size:16px;color:var(--muted)">${esc(car.variant_engine)}</span>` : ""}</h1>
    ${specLine ? `<p class="spec">${specLine}</p>` : ""}

    <h2>Vehicle details</h2>
    <div class="grid">
      ${details.map(([k, v]) => `<div class="dl"><span class="k">${k}</span><span class="v num">${v}</span></div>`).join("")}
    </div>

    <h2>Overall assessment</h2>
    <div><span class="conf ${confidence.flag}">${esc(CONFIDENCE_LABEL[confidence.flag])}</span></div>
    ${confidence.reasons ? `<p class="reasons">${esc(confidence.reasons)}</p>` : ""}
    ${warnings}
    ${
      agreement && cross_source.median_spread_pct !== null
        ? `<p class="agreement">${esc(agreement)} (${Math.abs(cross_source.median_spread_pct).toFixed(0)}% median spread${
            cross_source.ab_vs_bz_pct_gap !== null
              ? `, ${Math.abs(cross_source.ab_vs_bz_pct_gap).toFixed(1)}% valuation gap`
              : ""
          }).</p>`
        : ""
    }

    <h2>Autobazar.eu &amp; Bazoš.sk — shown separately, never merged</h2>
    ${sourceSection("Autobazar.eu", "autobazar.eu", sources.autobazar, car.asking_price_eur)}
    ${sourceSection("Bazoš.sk", "bazos.sk", sources.bazos, car.asking_price_eur)}

    <div class="foot">
      Each marketplace is evaluated independently — Carval never blends them into a single number.
      &ldquo;Below market&rdquo; means priced under that market&rsquo;s median asking price. The headline
      confidence uses the marketplace with the stronger comparable sample.
    </div>
  </div>
</body></html>`
}

export function exportSingleCarReport(result: CompareResult) {
  const html = singleCarReportHtml(result)
  const name = `carval-report-${slug(`${result.car.brand}-${result.car.model}`)}-${todayISO()}.html`
  downloadBlob(new Blob([html], { type: "text/html;charset=utf-8" }), name)
}

/* ========================================================================== */
/* 2 — INVENTORY: Excel (.xlsx) export, two worksheets                        */
/* ========================================================================== */
/** Worst (most serious) mileage status across the two independent sources. */
function worstMileage(car: AnalysisCarResult): { match: MileageMatch; source: string } {
  const ab = car.autobazar.mileage_match
  const bz = car.bazos.mileage_match
  if (MILEAGE_SEVERITY[ab] >= MILEAGE_SEVERITY[bz]) return { match: ab, source: "Autobazar.eu" }
  return { match: bz, source: "Bazoš.sk" }
}

/** Concise "important warning / insufficient-data" status string. */
function warningStatus(car: AnalysisCarResult): string {
  const parts: string[] = []
  if (car.confidence_flag === "INSUFFICIENT") parts.push("Insufficient comparable data")
  const check = (label: string, s: AnalysisSourceResult) => {
    if (s.error) parts.push(s.error.startsWith("BLOCKED") ? `${label} blocked` : `${label} error`)
    else if (s.comparable_count === 0) parts.push(`${label} no comparables`)
    if (s.mileage_match === "very_large" || s.mileage_match === "large")
      parts.push(`${label} mileage ${MILEAGE_LABEL[s.mileage_match].toLowerCase()}`)
  }
  check("Autobazar.eu", car.autobazar)
  check("Bazoš.sk", car.bazos)
  return parts.join("; ")
}

function vehicleName(car: AnalysisCarResult): string {
  return [car.brand, car.model, car.variant].filter(Boolean).join(" ")
}

function inventorySheets(cars: AnalysisCarResult[]): { name: string; rows: CellValue[][] }[] {
  const header: CellValue[] = [
    "Vehicle",
    "Brand",
    "Model",
    "Variant",
    "Year",
    "Fuel",
    "Mileage (km)",
    "Asking price (€)",
    "Autobazar.eu median (€)",
    "Autobazar.eu diff (€)",
    "Autobazar.eu diff (%)",
    "Autobazar.eu comparables",
    "Bazoš.sk median (€)",
    "Bazoš.sk diff (€)",
    "Bazoš.sk diff (%)",
    "Bazoš.sk comparables",
    "Confidence",
    "Mileage similarity",
    "Warnings / insufficient data",
  ]

  const main: CellValue[][] = [header]
  cars.forEach((car) => {
    const abDiff = diffEur(car.autobazar.median_eur, car.asking_price_eur)
    const bzDiff = diffEur(car.bazos.median_eur, car.asking_price_eur)
    const mile = worstMileage(car)
    main.push([
      vehicleName(car),
      car.brand,
      car.model,
      car.variant ?? "",
      car.year,
      car.fuel ?? "",
      car.km,
      round(car.asking_price_eur),
      round(car.autobazar.median_eur),
      round(abDiff),
      car.autobazar.price_diff_pct,
      car.autobazar.comparable_count,
      round(car.bazos.median_eur),
      round(bzDiff),
      car.bazos.price_diff_pct,
      car.bazos.comparable_count,
      CONFIDENCE_LABEL[car.confidence_flag],
      `${mile.source}: ${MILEAGE_LABEL[mile.match]}`,
      warningStatus(car),
    ])
  })

  // Second worksheet: comparable listing links kept per source (the inventory
  // result only carries example links, so price/year/mileage/title are left to
  // the detailed single-car report where full comparables are available).
  const compsHeader: CellValue[] = [
    "Vehicle",
    "Source",
    "Listing price (€)",
    "Year",
    "Mileage (km)",
    "Listing title",
    "Listing URL",
  ]
  const comps: CellValue[][] = [compsHeader]
  cars.forEach((car) => {
    const add = (label: string, s: AnalysisSourceResult) => {
      s.example_links.forEach((url) => comps.push([vehicleName(car), label, null, null, null, "", url]))
    }
    add("Autobazar.eu", car.autobazar)
    add("Bazoš.sk", car.bazos)
  })

  return [
    { name: "Inventory", rows: main },
    { name: "Comparable listings", rows: comps },
  ]
}

export function exportInventoryXlsx(cars: AnalysisCarResult[]) {
  const blob = buildXlsxBlob(inventorySheets(cars))
  downloadBlob(blob, `carval-inventory-${todayISO()}.xlsx`)
}

/* ========================================================================== */
/* 3 — INVENTORY: "Open results ↗" — dedicated scannable view in a new tab    */
/* ========================================================================== */
function invSourceCell(s: AnalysisSourceResult, asking: number | null): string {
  if (s.error && s.comparable_count === 0) {
    return `<span class="faint">${s.error.startsWith("BLOCKED") ? "blocked" : "no data"}</span>`
  }
  if (s.comparable_count === 0) return `<span class="faint">—</span>`
  const tone = toneClass(s.price_diff_pct)
  const d = diffEur(s.median_eur, asking)
  return `<div class="srccell">
    <span class="num">${eur(s.median_eur)}</span>
    <span class="num ${tone}">${pct(s.price_diff_pct)} · ${signedEur(d)}</span>
    <span class="faint">n=${s.comparable_count}</span>
  </div>`
}

function invDetail(car: AnalysisCarResult): string {
  const src = (label: string, s: AnalysisSourceResult) => {
    const links = s.example_links
      .slice(0, 3)
      .map((u) => `<a href="${esc(u)}" target="_blank" rel="noreferrer">${esc(u)}</a>`)
      .join("")
    const mileageNote =
      s.comparable_count > 0
        ? `<div class="mline">Mileage: ${esc(MILEAGE_LABEL[s.mileage_match])}${
            s.comp_km_median != null
              ? ` · comps ${km(s.comp_km_p25)}–${km(s.comp_km_p75)} (median ${km(s.comp_km_median)})`
              : ""
          }</div>`
        : ""
    return `<div class="dcol">
      <div class="dhead">${esc(label)} ${s.tier ? `<span class="pill">${esc(tierLabel(s.tier))}</span>` : ""}<span class="pill">n=${s.comparable_count}</span></div>
      ${s.error ? `<div class="faint">${esc(s.error)}</div>` : ""}
      ${s.comparable_count > 0 ? `<div class="mline">Median <span class="num">${eur(s.median_eur)}</span> · <span class="num ${toneClass(s.price_diff_pct)}">${pct(s.price_diff_pct)} ${esc(diffWord(s.price_diff_pct))}</span></div>` : ""}
      ${mileageNote}
      ${links ? `<div class="links">${links}</div>` : ""}
    </div>`
  }
  return `<td colspan="7"><div class="detail">
    ${src("Autobazar.eu", car.autobazar)}
    ${src("Bazoš.sk", car.bazos)}
    <div class="dmeta">
      <div><span class="faint">Why this confidence:</span> ${esc(car.confidence_reasons || "—")}</div>
      ${car.median_spread_pct != null ? `<div><span class="faint">Cross-source median spread:</span> ${Math.abs(car.median_spread_pct).toFixed(0)}% (shown, never averaged)</div>` : ""}
      ${car.missing_critical_fields && car.missing_critical_fields !== "none" ? `<div><span class="faint">Missing inventory fields:</span> ${esc(car.missing_critical_fields)}</div>` : ""}
    </div>
  </div></td>`
}

function inventoryResultsHtml(cars: AnalysisCarResult[], summary: AnalysisSummary | null): string {
  const counts = summary?.counts
  const chips = counts
    ? [
        ["Analyzed", counts.analyzed, "accent"],
        ["High", counts.high, "pos"],
        ["Medium", counts.medium, "accent"],
        ["Low", counts.low, "caution"],
        ["Insufficient", counts.insufficient, "muted"],
      ]
    : [["Analyzed", cars.length, "accent"]]

  const rows = cars
    .map((car, i) => {
      const specs = [yr(car.year), car.fuel, car.km != null ? km(car.km) : null]
        .filter((x) => x && x !== "—")
        .map(esc)
        .join(" · ")
      const mile = worstMileage(car)
      const mileSerious = mile.match === "very_large" || mile.match === "large"
      return `<tbody class="grp">
        <tr class="row" onclick="this.parentNode.classList.toggle('open')">
          <td class="rank num">${i + 1}</td>
          <td class="veh"><div class="vname">${esc(vehicleName(car))}</div><div class="faint specs">${specs || "—"}</div></td>
          <td class="num asking">${eur(car.asking_price_eur)}</td>
          <td>${invSourceCell(car.autobazar, car.asking_price_eur)}</td>
          <td>${invSourceCell(car.bazos, car.asking_price_eur)}</td>
          <td><span class="conf ${car.confidence_flag}">${esc(CONFIDENCE_LABEL[car.confidence_flag])}</span></td>
          <td>${mileSerious ? `<span class="mbadge">⚠ ${esc(MILEAGE_LABEL[mile.match])}</span>` : `<span class="faint">—</span>`}</td>
        </tr>
        <tr class="detrow">${invDetail(car)}</tr>
      </tbody>`
    })
    .join("")

  return `<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Carval — inventory results (${cars.length})</title>
<style>
  :root{--bg:#14181d;--surface:#1c2127;--surface2:#232a31;--line:#2f3944;--line2:#3c4753;
    --fg:#eef1f3;--muted:#aab4bd;--faint:#7d8792;--accent:#35c4d4;
    --pos:#2fd08a;--neg:#f0925a;--caution:#e6c34d;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
    font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-size:14px;
    -webkit-font-smoothing:antialiased}
  .num{font-variant-numeric:tabular-nums;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .pos{color:var(--pos)}.neg{color:var(--neg)}.neutral{color:var(--muted)}.faint{color:var(--faint)}
  .wrap{max-width:1100px;margin:0 auto;padding:28px 22px 80px}
  .topbar{display:flex;justify-content:space-between;align-items:baseline;
    border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:18px}
  .brand{font-size:20px;font-weight:600}.brand span{color:var(--accent)}
  .doc-meta{font-size:12px;color:var(--faint)}
  h1{font-size:22px;margin:0 0 14px}
  .chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px}
  .chip{display:flex;align-items:baseline;gap:7px;border:1px solid var(--line);background:var(--surface2);
    border-radius:7px;padding:6px 11px}
  .chip .n{font-family:ui-monospace,monospace;font-size:17px;font-weight:600}
  .chip .l{font-size:12px;color:var(--faint)}
  .chip.pos .n{color:var(--pos)}.chip.accent .n{color:var(--accent)}
  .chip.caution .n{color:var(--caution)}.chip.muted .n{color:var(--muted)}
  .hint{color:var(--faint);font-size:12px;margin:0 0 10px}
  table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--line);border-radius:10px;overflow:hidden}
  thead th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:var(--faint);
    font-weight:500;padding:10px 12px;background:var(--surface2);border-bottom:1px solid var(--line)}
  thead th.num{text-align:right}
  .row{cursor:pointer;border-top:1px solid var(--line)}
  .grp:first-child .row{border-top:0}
  .row:hover{background:var(--surface2)}
  .row td{padding:10px 12px;vertical-align:top}
  td.rank{color:var(--faint)}
  .vname{font-weight:500}.specs{font-size:12px;margin-top:2px}
  td.asking{text-align:right;font-weight:500}
  .srccell{display:flex;flex-direction:column;gap:2px;font-size:13px}
  .conf{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:500;
    padding:3px 9px;border-radius:999px;border:1px solid var(--line2)}
  .conf.HIGH{color:var(--pos);border-color:color-mix(in srgb,var(--pos) 40%,transparent)}
  .conf.MEDIUM{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 40%,transparent)}
  .conf.LOW{color:var(--caution);border-color:color-mix(in srgb,var(--caution) 40%,transparent)}
  .conf.INSUFFICIENT{color:var(--faint)}
  .mbadge{color:var(--neg);font-size:12px}
  .detrow{display:none}
  .grp.open .detrow{display:table-row}
  .detail{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:6px 12px 16px;background:var(--surface2)}
  .dcol{display:flex;flex-direction:column;gap:6px}
  .dhead{font-weight:500;display:flex;align-items:center;gap:6px}
  .mline{font-size:13px;color:var(--muted)}
  .pill{font-size:10px;font-family:ui-monospace,monospace;text-transform:uppercase;letter-spacing:0.04em;
    border:1px solid var(--line2);border-radius:4px;padding:1px 5px;color:var(--muted)}
  .links{display:flex;flex-direction:column;gap:1px;margin-top:2px}
  .links a{color:var(--accent);font-size:12px;text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .dmeta{grid-column:1/-1;border-top:1px solid var(--line);padding-top:10px;
    display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--muted)}
  @media (max-width:720px){.detail{grid-template-columns:1fr}}
</style></head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div class="brand">Car<span>val</span></div>
      <div class="doc-meta">Inventory results · generated ${esc(todayISO())}</div>
    </div>
    <h1>${cars.length} ${cars.length === 1 ? "vehicle" : "vehicles"} ranked — most below market first</h1>
    <div class="chips">
      ${chips.map(([l, n, t]) => `<div class="chip ${t}"><span class="n">${n}</span><span class="l">${l}</span></div>`).join("")}
    </div>
    <p class="hint">Click any row to expand source details and comparable listings. Autobazar.eu and Bazoš.sk are shown separately — never blended.</p>
    <table>
      <thead><tr>
        <th class="num">#</th><th>Vehicle</th><th class="num">Asking</th>
        <th>Autobazar.eu</th><th>Bazoš.sk</th><th>Confidence</th><th>Mileage</th>
      </tr></thead>
      ${rows}
    </table>
  </div>
</body></html>`
}

export function openInventoryResults(cars: AnalysisCarResult[], summary: AnalysisSummary | null) {
  openHtmlInNewTab(inventoryResultsHtml(cars, summary))
}
