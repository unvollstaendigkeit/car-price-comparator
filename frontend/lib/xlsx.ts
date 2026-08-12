/**
 * Minimal, dependency-free .xlsx (SpreadsheetML) writer.
 *
 * Produces a genuine multi-sheet workbook using inline strings, packaged as a
 * STORED (uncompressed) ZIP with a correct CRC-32 and central directory. This
 * lets us ship real Excel exports without pulling in a heavy spreadsheet
 * dependency just for this feature. It intentionally supports only what the
 * export needs: text + number cells across one or more sheets.
 */

export type CellValue = string | number | null | undefined

export interface Sheet {
  /** Worksheet tab name (Excel truncates to 31 chars). */
  name: string
  /** Row-major grid. First row is treated as a header like any other. */
  rows: CellValue[][]
}

/* -------------------------------------------------------------------------- */
/* XML helpers                                                                */
/* -------------------------------------------------------------------------- */
function xmlEscape(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    // Drop characters that are invalid in XML 1.0 (keep tab, LF, CR).
    .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g, "")
}

/** 0 -> "A", 25 -> "Z", 26 -> "AA" … */
function colLetter(index: number): string {
  let s = ""
  let n = index + 1
  while (n > 0) {
    const rem = (n - 1) % 26
    s = String.fromCharCode(65 + rem) + s
    n = Math.floor((n - 1) / 26)
  }
  return s
}

function sheetXml(rows: CellValue[][]): string {
  const parts: string[] = [
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
  ]
  rows.forEach((row, r) => {
    const rowNum = r + 1
    const cells: string[] = []
    row.forEach((val, c) => {
      if (val === null || val === undefined || val === "") return
      const ref = colLetter(c) + rowNum
      if (typeof val === "number" && Number.isFinite(val)) {
        cells.push(`<c r="${ref}"><v>${val}</v></c>`)
      } else {
        cells.push(
          `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${xmlEscape(String(val))}</t></is></c>`,
        )
      }
    })
    parts.push(`<row r="${rowNum}">${cells.join("")}</row>`)
  })
  parts.push("</sheetData></worksheet>")
  return parts.join("")
}

/* -------------------------------------------------------------------------- */
/* ZIP (store / no compression) + CRC-32                                      */
/* -------------------------------------------------------------------------- */
const CRC_TABLE = (() => {
  const t = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    t[n] = c >>> 0
  }
  return t
})()

function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

interface ZipEntry {
  name: string
  data: Uint8Array
}

function pushU16(arr: number[], v: number) {
  arr.push(v & 0xff, (v >>> 8) & 0xff)
}
function pushU32(arr: number[], v: number) {
  arr.push(v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff)
}
function pushBytes(arr: number[], b: Uint8Array) {
  for (let i = 0; i < b.length; i++) arr.push(b[i])
}

function zip(entries: ZipEntry[]): Uint8Array {
  const enc = new TextEncoder()
  const out: number[] = []
  const central: number[] = []
  let offset = 0

  for (const e of entries) {
    const nameBytes = enc.encode(e.name)
    const crc = crc32(e.data)
    const size = e.data.length
    const headerOffset = offset

    // Local file header
    const local: number[] = []
    pushU32(local, 0x04034b50)
    pushU16(local, 20) // version needed to extract
    pushU16(local, 0) // general purpose flag
    pushU16(local, 0) // compression method: store
    pushU16(local, 0) // mod time
    pushU16(local, 0) // mod date
    pushU32(local, crc)
    pushU32(local, size) // compressed size
    pushU32(local, size) // uncompressed size
    pushU16(local, nameBytes.length)
    pushU16(local, 0) // extra length
    pushBytes(local, nameBytes)

    pushBytes(out, Uint8Array.from(local))
    offset += local.length
    pushBytes(out, e.data)
    offset += size

    // Central directory record
    pushU32(central, 0x02014b50)
    pushU16(central, 20) // version made by
    pushU16(central, 20) // version needed
    pushU16(central, 0) // flag
    pushU16(central, 0) // method
    pushU16(central, 0) // mod time
    pushU16(central, 0) // mod date
    pushU32(central, crc)
    pushU32(central, size)
    pushU32(central, size)
    pushU16(central, nameBytes.length)
    pushU16(central, 0) // extra
    pushU16(central, 0) // comment
    pushU16(central, 0) // disk number start
    pushU16(central, 0) // internal attrs
    pushU32(central, 0) // external attrs
    pushU32(central, headerOffset)
    pushBytes(central, nameBytes)
  }

  const centralOffset = offset
  const centralSize = central.length
  pushBytes(out, Uint8Array.from(central))

  // End of central directory
  const end: number[] = []
  pushU32(end, 0x06054b50)
  pushU16(end, 0) // disk number
  pushU16(end, 0) // disk w/ central dir
  pushU16(end, entries.length)
  pushU16(end, entries.length)
  pushU32(end, centralSize)
  pushU32(end, centralOffset)
  pushU16(end, 0) // comment length
  pushBytes(out, Uint8Array.from(end))

  return Uint8Array.from(out)
}

/* -------------------------------------------------------------------------- */
/* Public API                                                                 */
/* -------------------------------------------------------------------------- */
export function buildXlsxBlob(sheets: Sheet[]): Blob {
  const enc = new TextEncoder()
  const entries: ZipEntry[] = []

  const contentTypes =
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">` +
    `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>` +
    `<Default Extension="xml" ContentType="application/xml"/>` +
    `<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>` +
    sheets
      .map(
        (_, i) =>
          `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`,
      )
      .join("") +
    `</Types>`

  const rootRels =
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
    `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>` +
    `</Relationships>`

  const workbook =
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>` +
    sheets
      .map(
        (s, i) =>
          `<sheet name="${xmlEscape(s.name).slice(0, 31)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`,
      )
      .join("") +
    `</sheets></workbook>`

  const workbookRels =
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
    `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
    sheets
      .map(
        (_, i) =>
          `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`,
      )
      .join("") +
    `</Relationships>`

  entries.push({ name: "[Content_Types].xml", data: enc.encode(contentTypes) })
  entries.push({ name: "_rels/.rels", data: enc.encode(rootRels) })
  entries.push({ name: "xl/workbook.xml", data: enc.encode(workbook) })
  entries.push({ name: "xl/_rels/workbook.xml.rels", data: enc.encode(workbookRels) })
  sheets.forEach((s, i) => {
    entries.push({ name: `xl/worksheets/sheet${i + 1}.xml`, data: enc.encode(sheetXml(s.rows)) })
  })

  return new Blob([zip(entries)], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  })
}
