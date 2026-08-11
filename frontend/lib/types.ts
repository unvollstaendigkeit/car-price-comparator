/**
 * Types mirroring the backend contract (single_car.compare_single_car and
 * inventory_normalizer.normalize_inventory). Autobazar and Bazoš are always
 * kept as two independent source blocks — never merged.
 */

export interface Comparable {
  price: number | null
  year: number | null
  km: number | null
  url: string | null
  title: string | null
  fuel?: string | null
  power_kw?: number | null
  transmission?: string | null
  body_type?: string | null
}

export interface SourceResult {
  tier: string | null
  comparable_count: number
  median_asking_eur: number | null
  market_p25_eur: number | null
  market_p75_eur: number | null
  price_difference_eur: number | null
  undervaluation_pct: number | null
  insufficient: boolean
  unknown_year_km_frac: number
  outliers_trimmed: number
  sample_warning: string
  retrieval_error: string | null
  comparables: Comparable[]
}

export type ConfidenceFlag = 'HIGH' | 'MEDIUM' | 'LOW' | 'INSUFFICIENT'
export type Agreement = 'agree' | 'meaningful' | 'large' | null

export interface CarEcho {
  brand: string
  model: string
  variant: string | null
  variant_engine: string | null
  year: number | null
  fuel: string | null
  km: number | null
  asking_price_eur: number | null
  power_kw: number | null
  power_source: string
  transmission: string | null
  body_type: string | null
}

export interface CompareResult {
  car: CarEcho
  sources: {
    autobazar: SourceResult
    bazos: SourceResult
  }
  cross_source: {
    median_spread_pct: number | null
    agreement: Agreement
    ab_vs_bz_pct_gap: number | null
  }
  confidence: {
    flag: ConfidenceFlag
    reasons: string
    warnings: string[]
  }
  insufficient: boolean
  primary_source_for_ranking: string | null
  primary_undervaluation_pct: number | null
  missing_critical_fields: string
}

export type SourceKey = 'autobazar' | 'bazos'

export const SOURCE_META: Record<SourceKey, { label: string; host: string }> = {
  autobazar: { label: 'Autobazar.eu', host: 'autobazar.eu' },
  bazos: { label: 'Bazoš.sk', host: 'bazos.sk' },
}

/* ---- streaming progress ---- */
export type Stage =
  | 'preparing'
  | 'prepared'
  | 'searching_autobazar'
  | 'autobazar_done'
  | 'searching_bazos'
  | 'bazos_done'
  | 'comparing'
  | 'finalizing'
  | 'result'
  | 'error'

export interface ProgressEvent {
  stage: Stage
  label: string
  car?: Partial<CarEcho>
  raw_count?: number
  error?: string | null
  message?: string
  result?: CompareResult
}

export interface CarInput {
  brand: string
  model: string
  variant?: string
  year?: number
  fuel?: string
  km?: number
  price?: number
  power_kw?: number
  transmission?: string
  body_type?: string
}

/* ---- paste-a-row parsing (backend row_parser.parse_pasted_row) ---- */
export type FieldConfidence = 'high' | 'medium' | 'low'

export interface ParsedField {
  value: string | number | null
  detected: boolean
  confidence: FieldConfidence
  source: string
}

export interface ParsedRow {
  ok: boolean
  mode: 'header' | 'headerless' | 'empty'
  car: CarInput
  fields: Record<keyof CarInput, ParsedField>
  extras: {
    vin?: string | null
    colour?: string | null
    notes?: string[]
  }
  cells: string[]
  issues: string[]
}
