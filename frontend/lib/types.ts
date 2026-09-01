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

/* How well this source's comparable mileage matches the submitted car.
 * 'good' | 'moderate' | 'large' | 'very_large' | 'unknown'. This is an explicit
 * confidence factor and NEVER changes the price figures. Kept per source —
 * Autobazar and Bazoš mileage evidence are never merged. */
export type MileageMatch = 'good' | 'moderate' | 'large' | 'very_large' | 'unknown'

export interface MileageAssessment {
  category: MileageMatch
  comp_km_count: number
  comp_km_median: number | null
  comp_km_p25: number | null
  comp_km_p75: number | null
  submitted_km: number | null
  rel_gap: number | null
  abs_gap_km: number | null
  close_frac: number | null
  direction: 'lower' | 'higher' | 'same' | 'unknown'
  note: string
}

/* Structured, code-only warnings. Never carry backend prose (exception text,
 * ratios, "n=1" jargon) - the frontend owns all wording for these, keyed off
 * `code` (+ `reason` for retrieval failures). See lib/warning-copy.ts. */
export type SampleWarningCode = 'single_listing' | 'implausible_ratio'
export interface SampleWarning {
  code: SampleWarningCode
}
export type RetrievalFailureReason = 'timeout' | 'blocked'
export interface RetrievalIssue {
  code: 'retrieval_failed'
  reason: RetrievalFailureReason
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
  sample_warnings: SampleWarning[]
  retrieval_issue: RetrievalIssue | null
  /** Raw backend error text - only for an explicit, opt-in "show error detail" affordance. Never render by default. */
  retrieval_error: string | null
  mileage_match: MileageMatch
  mileage: MileageAssessment
  comparables: Comparable[]
}

export type ConfidenceFlag = 'HIGH' | 'MEDIUM' | 'LOW' | 'INSUFFICIENT'
export type Agreement = 'agree' | 'meaningful' | 'large' | null

export type ConfidenceWarning =
  | { code: SampleWarningCode; source: SourceKey }
  | { code: 'retrieval_failed'; source: SourceKey; reason: RetrievalFailureReason }
  | { code: 'source_disagreement'; spread_pct: number }

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
    warnings: ConfidenceWarning[]
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
  /** Passthrough only — detected from a pasted row, never sent to matching logic. */
  vin?: string
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

/* ---- inventory upload (backend inventory_api.parse_upload / parse_demo) ----
 * Parsing + validation ONLY. No marketplace scraping happens in this phase. */
export type InventoryRowStatus = 'ready' | 'sold' | 'review'

export interface InventoryRow {
  row_index: number
  row_number: number
  brand: string | null
  model: string | null
  variant: string | null
  year: number | null
  fuel: string | null
  km: number | null
  price: number | null
  body_type: string | null
  vin: string | null
  colour: string | null
  status: string | null
  status_label: InventoryRowStatus
  valid_for_comparison: boolean
  issues: string[]
  price_original: string | null
  km_original: string | null
  year_source: string | null
}

export interface InventoryCounts {
  total_rows: number
  valid_for_comparison: number
  sold_or_unavailable: number
  invalid: number
  review: number
}

export interface InventoryReport {
  ok: boolean
  source_name: string
  counts: InventoryCounts
  mapping: { field: string; column: string }[]
  unmapped_columns: string[]
  missing_required: string[]
  ambiguities: string[]
  detected_columns: string[]
  required_fields: string[]
  rows: InventoryRow[]
}

/* ---- inventory batch market analysis (backend inventory_run) ----
 * DOES retrieve from the marketplaces, grouped per (brand, model). Autobazar
 * and Bazoš are kept fully separate — never merged into one price.
 * (ConfidenceFlag is defined above and reused here.) */
export interface AnalysisSourceResult {
  median_eur: number | null
  price_diff_pct: number | null
  comparable_count: number
  tier: string | null
  insufficient: boolean
  example_links: string[]
  error: string | null
  mileage_match: MileageMatch
  comp_km_median: number | null
  comp_km_p25: number | null
  comp_km_p75: number | null
  mileage_note: string
}

export interface AnalysisCarResult {
  row_index: number
  row_number: number
  brand: string
  model: string
  variant: string | null
  year: number | null
  km: number | null
  fuel: string | null
  power_kw: number | null
  asking_price_eur: number | null
  autobazar: AnalysisSourceResult
  bazos: AnalysisSourceResult
  median_spread_pct: number | null
  ab_vs_bz_pct_gap: number | null
  rank_source: string | null
  rank_price_diff_pct: number | null
  confidence_flag: ConfidenceFlag
  confidence_reasons: string
  missing_critical_fields: string
}

export interface AnalysisSummary {
  counts: {
    total_cars: number
    analyzed: number
    high: number
    medium: number
    low: number
    insufficient: number
  }
  market_lookups: number
  disabled_sources: string[]
  /** Per-source soft timeouts (deadline overruns) and hard block/errors. */
  timeouts?: Record<string, number>
  errors?: Record<string, number>
  timeouts_total?: number
  errors_total?: number
  /** Models that hit the whole-model wall-clock budget (safety net firing). */
  model_timeouts?: number
  model_budget_s?: number
  ranked: AnalysisCarResult[]
  benchmark?: {
    total_cars: number
    unique_groups: number
    cached_groups: number
    cache_hits: number
    http_requests: number
    http_requests_measured?: number
    market_lookups: number
    retrieval_time_s: number
    eval_time_s: number
    total_time_s: number
    avg_http_time_s?: number
    timeouts_total?: number
    errors_total?: number
    model_timeouts?: number
    mode: string
  }
}

/** Per-source cache status the backend reports for a model before retrieval. */
export interface CacheSourceStatus {
  from_cache: boolean
  age_s: number | null
}

/** Union of every event the /api/inventory/analyze/stream SSE emits. */
export type AnalysisEvent =
  | { stage: 'start'; total_cars: number; total_groups: number }
  | {
      stage: 'group_start'
      group_index: number
      group_total: number
      brand: string
      model: string
      cars_in_group: number
      cached: boolean
      cache_status: { autobazar: CacheSourceStatus; bazos: CacheSourceStatus }
    }
  | {
      stage: 'retrieved'
      brand: string
      model: string
      ab_count: number
      bz_count: number
      ab_error: string | null
      bz_error: string | null
      ab_from_cache: boolean
      bz_from_cache: boolean
      ab_age_s: number | null
      bz_age_s: number | null
      ab_timed_out: boolean
      bz_timed_out: boolean
    }
  | {
      stage: 'source_timeout'
      source: string
      brand: string
      model: string
      kept: number
      reason: string
    }
  | {
      stage: 'group_timeout'
      brand: string
      model: string
      budget_s: number
      elapsed_s: number
      ab_kept: number
      bz_kept: number
      reason: string
    }
  | { stage: 'source_disabled'; source: string; reason: string }
  | { stage: 'car_done'; analyzed: number; total: number; car: AnalysisCarResult }
  | ({ stage: 'summary' } & AnalysisSummary)
  | { stage: 'error'; label?: string; message: string }
