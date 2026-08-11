import type {
  AnalysisEvent,
  CarInput,
  InventoryReport,
  InventoryRow,
  ParsedRow,
  ProgressEvent,
} from './types'

/**
 * Read an SSE response body and invoke `onEvent` for each `data:` frame as it
 * arrives. Shared by every streaming endpoint so the parsing logic lives once.
 */
async function readSSE<T>(res: Response, onEvent: (e: T) => void): Promise<void> {
  if (!res.ok || !res.body) {
    throw new Error(await errorMessage(res, `Backend returned ${res.status}`))
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE frames are separated by a blank line.
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const line = frame.split('\n').find((l) => l.startsWith('data:'))
      if (!line) continue
      const json = line.slice(5).trim()
      if (!json) continue
      try {
        onEvent(JSON.parse(json) as T)
      } catch {
        // ignore malformed frame; stream continues
      }
    }
  }
}

/** Turn a non-2xx response into a readable message, preferring FastAPI's `detail`. */
async function errorMessage(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json()
    if (data && typeof data.detail === 'string') return data.detail
  } catch {
    // not JSON — fall through
  }
  return fallback
}

/**
 * Upload a dealer inventory spreadsheet (.csv / .xlsx) and get back a
 * normalized + validated review report. PARSING ONLY — no marketplace scraping
 * happens; the backend reuses the same canonical normalizer as the single-car
 * path so there is one methodology across the app.
 */
export async function parseInventory(file: File): Promise<InventoryReport> {
  const body = new FormData()
  body.append('file', file)
  const res = await fetch('/api/inventory/parse', { method: 'POST', body })
  if (!res.ok) {
    throw new Error(await errorMessage(res, `Backend returned ${res.status}`))
  }
  return (await res.json()) as InventoryReport
}

/** Load a bundled demo inventory ("sample" = 98 cars, "alt" = alternative format). */
export async function loadInventoryDemo(name: 'sample' | 'alt' = 'sample'): Promise<InventoryReport> {
  const res = await fetch(`/api/inventory/demo?name=${encodeURIComponent(name)}`)
  if (!res.ok) {
    throw new Error(await errorMessage(res, `Backend returned ${res.status}`))
  }
  return (await res.json()) as InventoryReport
}

/**
 * Parse a single pasted spreadsheet / Markdown / delimited row into the
 * canonical single-car fields. Pure parsing (no scraping) — the backend
 * reuses the shared inventory normalizer so it never diverges from the
 * manual entry path.
 */
export async function parseRow(text: string): Promise<ParsedRow> {
  const res = await fetch('/api/parse-row', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!res.ok) {
    throw new Error(`Backend returned ${res.status}`)
  }
  return (await res.json()) as ParsedRow
}

/**
 * Stream a single-car comparison. Vercel routes /api/* to the FastAPI backend,
 * which strips the /api prefix. The backend emits Server-Sent Events, each a
 * real engine stage; `onEvent` fires for every stage as it happens so the UI
 * never freezes during the ~30s live retrieval.
 */
export async function streamCompare(
  input: CarInput,
  onEvent: (e: ProgressEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/compare/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
    signal,
  })
  await readSSE<ProgressEvent>(res, onEvent)
}

/**
 * Stream a batch market analysis for a confirmed inventory. Unlike the parse
 * endpoints this DOES retrieve from the marketplaces, but the backend groups
 * cars per (brand, model) with a shared cache and polite pacing, so requests
 * scale with unique models rather than car count. `onEvent` fires for every
 * group/car/summary event as it happens.
 */
export async function streamInventoryAnalysis(
  rows: InventoryRow[],
  onEvent: (e: AnalysisEvent) => void,
  signal?: AbortSignal,
  options?: { refresh?: boolean },
): Promise<void> {
  const res = await fetch('/api/inventory/analyze/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows, refresh: options?.refresh ?? false }),
    signal,
  })
  await readSSE<AnalysisEvent>(res, onEvent)
}
