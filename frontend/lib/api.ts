import type { CarInput, ParsedRow, ProgressEvent } from './types'

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

  if (!res.ok || !res.body) {
    throw new Error(`Backend returned ${res.status}`)
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
        onEvent(JSON.parse(json) as ProgressEvent)
      } catch {
        // ignore malformed frame; stream continues
      }
    }
  }
}
