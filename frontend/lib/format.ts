import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function eur(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `€${Math.round(v).toLocaleString('en-US')}`
}

export function eurSigned(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const sign = v > 0 ? '+' : v < 0 ? '−' : ''
  return `${sign}€${Math.abs(Math.round(v)).toLocaleString('en-US')}`
}

export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const sign = v > 0 ? '+' : v < 0 ? '−' : ''
  return `${sign}${Math.abs(v).toFixed(1)}%`
}

export function km(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${Math.round(v).toLocaleString('en-US')} km`
}

export function num(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return String(v)
}

/**
 * undervaluation_pct sign convention (backend definition):
 *   positive -> cheaper than market median (a potential deal)
 *   negative -> more expensive than market median
 */
export function valuationTone(pctVal: number | null | undefined):
  | 'positive'
  | 'negative'
  | 'neutral' {
  if (pctVal === null || pctVal === undefined || Number.isNaN(pctVal)) return 'neutral'
  if (pctVal >= 2) return 'positive'
  if (pctVal <= -2) return 'negative'
  return 'neutral'
}
