import type { CarInput } from "@/lib/types"

/**
 * CarInput <-> URL query string, so a comparison can be handed off to its
 * own tab at its own URL (see single-car-module.tsx and app/result). The
 * URL carries the car's inputs, not the result itself — visiting/reloading
 * it re-runs a live comparison, same as submitting the form does today.
 */

const STRING_FIELDS = ["brand", "model", "variant", "fuel", "transmission", "body_type", "vin"] as const
const NUMBER_FIELDS = ["year", "km", "price", "power_kw"] as const

export function carInputToSearchParams(input: CarInput): URLSearchParams {
  const params = new URLSearchParams()
  for (const key of STRING_FIELDS) {
    const v = input[key]
    if (v) params.set(key, v)
  }
  for (const key of NUMBER_FIELDS) {
    const v = input[key]
    if (v !== undefined && v !== null && Number.isFinite(v)) params.set(key, String(v))
  }
  return params
}

/** Returns null when brand/model (the only required fields) are missing. */
export function carInputFromSearchParams(params: URLSearchParams): CarInput | null {
  const brand = params.get("brand")?.trim()
  const model = params.get("model")?.trim()
  if (!brand || !model) return null

  const num = (key: string): number | undefined => {
    const raw = params.get(key)
    if (!raw) return undefined
    const n = Number(raw)
    return Number.isFinite(n) ? n : undefined
  }

  return {
    brand,
    model,
    variant: params.get("variant") || undefined,
    year: num("year"),
    fuel: params.get("fuel") || undefined,
    km: num("km"),
    price: num("price"),
    power_kw: num("power_kw"),
    transmission: params.get("transmission") || undefined,
    body_type: params.get("body_type") || undefined,
    vin: params.get("vin") || undefined,
  }
}
