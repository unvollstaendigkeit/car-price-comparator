"""
Paste-a-row parsing layer.

Turns a SINGLE row copied from Excel / Google Sheets / a Markdown table into the
canonical single-car fields, WITHOUT requiring the user to include the header
row. It is a thin extension on top of `inventory_normalizer` rather than a
second parser:

  * When a header line IS present, we defer entirely to
    `inventory_normalizer.normalize_inventory` - the exact same header mapping
    and value-based disambiguation used by the bulk-upload path.
  * When only values are present (the common case), we infer each cell's field
    by CONTENT, reusing the normalizer's vocabularies and value parsers
    (`norm_year`, `norm_fuel`, `norm_body`, `norm_price`, the VIN pattern, ...).
    We never invent a value we cannot justify: cells assigned purely by position
    are flagged low-confidence, and unfilled recommended fields are reported as
    "not detected" for the user to correct.

Design principles are carried straight from the rest of the project: rule-based,
evidence over guessing, and fully auditable (every field records how it was
detected).

Public API:
    parse_pasted_row(text: str) -> ParsedRow
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

import pandas as pd

from inventory_normalizer import (
    _BODY_VOCAB,
    _TOKEN_TO_CANON,
    _clean_text,
    _norm_header,
    norm_body,
    norm_fuel,
    norm_price,
    norm_year,
    normalize_inventory,
)

# Canonical fields that the single-car form / comparison engine consumes.
CAR_FIELDS = ["brand", "model", "variant", "year", "fuel", "km", "price", "body_type"]
# Fields worth flagging as "not detected" when missing (drive a useful compare).
RECOMMENDED_FIELDS = ["brand", "model", "year", "fuel", "km", "price"]


# --------------------------------------------------------------------------- #
# Extra vocabularies for headerless inference.
#
# The normalizer never needed a brand or colour vocabulary (it maps by header),
# but headerless inference must recognise values on sight. Kept lowercase and
# deliberately extensible, mirroring the normalizer's vocabulary style.
# --------------------------------------------------------------------------- #
_BRAND_MAP = {
    # alias (lowercase) -> canonical display used for marketplace search
    "vw": "Volkswagen", "volkswagen": "Volkswagen",
    "mercedes": "Mercedes-Benz", "mercedes-benz": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz", "merc": "Mercedes-Benz", "benz": "Mercedes-Benz",
    "bmw": "BMW", "beemer": "BMW",
    "audi": "Audi", "skoda": "Skoda", "škoda": "Skoda",
    "seat": "Seat", "cupra": "Cupra",
    "toyota": "Toyota", "lexus": "Lexus",
    "honda": "Honda", "mazda": "Mazda", "nissan": "Nissan", "subaru": "Subaru",
    "mitsubishi": "Mitsubishi", "suzuki": "Suzuki", "isuzu": "Isuzu",
    "ford": "Ford", "opel": "Opel", "vauxhall": "Opel",
    "peugeot": "Peugeot", "citroen": "Citroen", "citroën": "Citroen", "ds": "DS",
    "renault": "Renault", "dacia": "Dacia",
    "fiat": "Fiat", "alfa": "Alfa Romeo", "alfa romeo": "Alfa Romeo",
    "lancia": "Lancia", "jeep": "Jeep",
    "hyundai": "Hyundai", "kia": "Kia", "genesis": "Genesis",
    "volvo": "Volvo", "polestar": "Polestar",
    "mini": "Mini", "smart": "Smart",
    "porsche": "Porsche", "jaguar": "Jaguar",
    "land rover": "Land Rover", "landrover": "Land Rover", "range rover": "Land Rover",
    "chevrolet": "Chevrolet", "chevy": "Chevrolet",
    "tesla": "Tesla", "cadillac": "Cadillac", "chrysler": "Chrysler",
    "dodge": "Dodge", "gmc": "GMC",
    "saab": "Saab", "mg": "MG", "byd": "BYD",
    "ssangyong": "SsangYong", "daihatsu": "Daihatsu", "infiniti": "Infiniti",
    "acura": "Acura", "abarth": "Abarth", "bentley": "Bentley",
    "maserati": "Maserati", "ferrari": "Ferrari", "lamborghini": "Lamborghini",
    "aston martin": "Aston Martin",
}
# Multi-word brands, longest first, so we can match them before single tokens.
_MULTIWORD_BRANDS = sorted(
    [b for b in _BRAND_MAP if " " in b], key=len, reverse=True
)

_COLOUR_MAP = {
    "black": "Black", "white": "White", "silver": "Silver", "grey": "Grey",
    "gray": "Grey", "red": "Red", "blue": "Blue", "green": "Green",
    "yellow": "Yellow", "orange": "Orange", "brown": "Brown", "beige": "Beige",
    "gold": "Gold", "bronze": "Bronze", "purple": "Purple", "violet": "Violet",
    "navy": "Navy", "burgundy": "Burgundy", "anthracite": "Anthracite",
    "champagne": "Champagne", "turquoise": "Turquoise", "maroon": "Maroon",
    # a few common non-English colours seen in EU dealer sheets
    "sort": "Black", "hvid": "White", "sølv": "Silver", "grå": "Grey",
    "rød": "Red", "blå": "Blue", "grøn": "Green",
    "cierna": "Black", "biela": "White", "strieborna": "Silver",
}

# 17-char VIN: no I, O, or Q (ISO 3779).
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.I)

# Currency / unit tokens that may decorate a numeric cell ("280,000 km", "€2 300").
_NUM_DECORATION_RE = re.compile(
    r"(?i)(€|£|\$|\beur\b|\bkr\b|\bkč\b|\bkc\b|\bczk\b|\bpln\b|zł|zl|\bvat\b|"
    r"\bkm\b|\bkms\b|\bkilometer\b|\bkilometre\b|\bkilometres\b|\bmiles\b|\bmi\b|,-)"
)
_KM_UNIT_RE = re.compile(r"\b(?:km|kms|kilometer|kilometre|kilometres|miles|mi)\b", re.I)
_CURRENCY_RE = re.compile(r"(?:€|£|\$|\beur\b|\bkr\b|\bkč\b|\bkc\b|\bczk\b|\bpln\b|\bvat\b|zł|,-)", re.I)

# Engine / trim signature -> marks a text cell as the variant.
_ENGINE_RE = re.compile(
    r"(\d[.,]\d)"                                   # displacement 1,4 / 2.0
    r"|(\b\d{2,3}\s?(?:hp|hk|ps|kw|bhp)\b)"          # power 90HK / 150 HP
    r"|\b(?:tdi|tsi|tfsi|fsi|hdi|dci|cdi|crdi|jtd|dtec|vtec|bmt|"
    r"dsg|s[- ]?tronic|tiptronic|4x4|4motion|quattro|xdrive|awd|hybrid|phev)\b"
    r"|\b\d{1,2}d\b",                                # 5d / 3d body-door suffix
    re.I,
)

# Markdown table separator row: |---|:--:|
_MD_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


# --------------------------------------------------------------------------- #
# Result object
# --------------------------------------------------------------------------- #
@dataclass
class FieldDetection:
    value: object                 # normalized value (or None)
    detected: bool                # did we fill it?
    confidence: str               # "high" | "low" | "none"
    source: str                   # how ("vin_pattern", "position", "header", ...)


@dataclass
class ParsedRow:
    ok: bool
    mode: str                                  # "header" | "headerless" | "header_only" | "empty"
    car: dict                                  # CarInput-shaped {field: value}
    fields: dict                               # field -> FieldDetection
    extras: dict                               # {vin, colour, notes} informational only
    cells: list                                # raw cells parsed (transparency)
    issues: list                               # human-readable notes
    not_detected: list                         # recommended fields left empty

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "car": self.car,
            "fields": {k: asdict(v) for k, v in self.fields.items()},
            "extras": self.extras,
            "cells": self.cells,
            "issues": self.issues,
            "not_detected": self.not_detected,
        }


# --------------------------------------------------------------------------- #
# Tokenization
# --------------------------------------------------------------------------- #
def _split_cells(line: str, drop_empty: bool) -> list[str]:
    """Split one pasted line into cells, auto-detecting the delimiter."""
    s = line.strip()
    if "|" in s:                                  # markdown / pipe-delimited
        s = s.strip("|")
        parts = [p.strip() for p in s.split("|")]
    elif "\t" in s:                               # Excel / Sheets paste
        parts = [p.strip() for p in s.split("\t")]
    else:                                          # runs of 2+ spaces (never 1,
        parts = [p.strip() for p in re.split(r" {2,}|\s*;\s*", s)]  # keeps variants intact)
    if drop_empty:
        return [p for p in parts if p]
    return parts


def _clean_lines(text: str) -> list[str]:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return [ln for ln in lines if not _MD_SEP_RE.match(ln)]


def _looks_like_header(cells: list[str]) -> bool:
    if len(cells) < 2:
        return False
    hits = sum(1 for c in cells if _norm_header(c) in _TOKEN_TO_CANON)
    return hits / len(cells) >= 0.5


# --------------------------------------------------------------------------- #
# Value classifiers (thin wrappers over the normalizer vocabularies)
# --------------------------------------------------------------------------- #
def _match_brand(cell: str) -> Optional[str]:
    t = (_clean_text(cell) or "").lower()
    if not t:
        return None
    if t in _BRAND_MAP:
        return _BRAND_MAP[t]
    for mb in _MULTIWORD_BRANDS:            # "land rover discovery" -> Land Rover
        if t == mb or t.startswith(mb + " "):
            return _BRAND_MAP[mb]
    first = t.split()[0]
    return _BRAND_MAP.get(first)


def _is_yearish(cell: str) -> bool:
    y, prov = norm_year(cell)
    return y is not None and prov != "unparsed"


def _is_numeric(cell: str) -> bool:
    """
    True when a cell is essentially a number (plain "120000", grouped "280,000",
    spaced "2 300", or decorated "€2,300" / "280000 km"). Rejects anything with
    residual letters (e.g. the variant "1,4 TDI 90HK") so numbers embedded in
    text are never mistaken for a bare mileage/price cell.
    """
    s = _NUM_DECORATION_RE.sub("", cell.strip()).strip().strip("-").strip()
    if not re.search(r"\d", s):
        return False
    return bool(re.fullmatch(r"\d[\d.,\s]*\d|\d", s))


# --------------------------------------------------------------------------- #
# Header path: reuse the inventory normalizer verbatim
# --------------------------------------------------------------------------- #
def _parse_with_header(header_cells: list[str], data_cells: list[str]) -> ParsedRow:
    n = len(header_cells)
    data = (list(data_cells) + [None] * n)[:n]
    df = pd.DataFrame([dict(zip(header_cells, data))], columns=header_cells)
    report = normalize_inventory(df)
    row = report.rows.iloc[0].to_dict()

    issues = list(report.ambiguities)
    if report.row_issues:
        issues.extend(report.row_issues[0]["issues"])

    return _shape(row, source="header", mode="header", cells=data, extra_issues=issues)


# --------------------------------------------------------------------------- #
# Headerless path: infer each field from cell content
# --------------------------------------------------------------------------- #
def _parse_headerless(cells: list[str]) -> ParsedRow:
    n = len(cells)
    used = [False] * n
    picked: dict[str, tuple[int, object, str, str]] = {}  # field -> (idx, value, conf, source)

    def take(field_name: str, idx: int, value, conf: str, source: str):
        picked[field_name] = (idx, value, conf, source)
        used[idx] = True

    # 1) VIN - unique 17-char pattern, unambiguous.
    for i, c in enumerate(cells):
        if not used[i] and _VIN_RE.match(re.sub(r"\s", "", c)):
            take("vin", i, re.sub(r"\s", "", c).upper(), "high", "vin_pattern")
            break

    # 2) Year - bare year or a registration date.
    for i, c in enumerate(cells):
        if used[i]:
            continue
        if _is_yearish(c):
            y, prov = norm_year(c)
            take("year", i, y, "high", f"year_pattern:{prov}")
            break

    # 3) Fuel - canonical fuel vocabulary.
    for i, c in enumerate(cells):
        if used[i]:
            continue
        if norm_fuel(c):
            take("fuel", i, norm_fuel(c), "high", "fuel_vocab")
            break

    # 4) Body type - body vocabulary.
    for i, c in enumerate(cells):
        if used[i]:
            continue
        if (_clean_text(c) or "").lower() in _BODY_VOCAB:
            take("body_type", i, norm_body(c), "high", "body_vocab")
            break

    # 5) Brand - brand vocabulary.
    brand_idx = -1
    for i, c in enumerate(cells):
        if used[i]:
            continue
        b = _match_brand(c)
        if b:
            take("brand", i, b, "high", "brand_vocab")
            brand_idx = i
            break

    # 6) Colour - colour vocabulary (informational).
    for i, c in enumerate(cells):
        if used[i]:
            continue
        if (_clean_text(c) or "").lower() in _COLOUR_MAP:
            take("colour", i, _COLOUR_MAP[(_clean_text(c) or "").lower()], "high", "colour_vocab")
            break

    # 7) Numeric cells -> km / price. Explicit unit/currency wins; otherwise the
    #    remaining numerics fill [km, price] in reading order (the layout both
    #    known table formats share). Position-only picks are flagged low-conf.
    numeric_idx = [i for i in range(n) if not used[i] and _is_numeric(cells[i])]
    for i in list(numeric_idx):
        if "km" not in picked and _KM_UNIT_RE.search(cells[i]):
            val, _, _ = norm_price(cells[i])
            take("km", i, val, "high", "km_unit")
            numeric_idx.remove(i)
    for i in list(numeric_idx):
        if "price" not in picked and _CURRENCY_RE.search(cells[i]):
            val, status, orig = norm_price(cells[i])
            take("price", i, val, "high", "price_currency")
            numeric_idx.remove(i)
    for i in numeric_idx:
        if "km" not in picked:
            val, _, _ = norm_price(cells[i])
            take("km", i, val, "low", "position_order")
        elif "price" not in picked:
            val, status, orig = norm_price(cells[i])
            take("price", i, val, "low", "position_order")

    # 8) Remaining free-text cells -> model / variant, trailing text -> notes.
    text_idx = [i for i in range(n) if not used[i] and (_clean_text(cells[i]) or "")]
    engine_idx = [i for i in text_idx if _ENGINE_RE.search(cells[i])]
    plain_idx = [i for i in text_idx if i not in engine_idx]

    if "model" not in picked:
        after = [i for i in plain_idx if brand_idx < 0 or i > brand_idx]
        model_pick = after[0] if after else (plain_idx[0] if plain_idx else None)
        if model_pick is not None:
            take("model", model_pick, _clean_text(cells[model_pick]), "low", "position")
    if "variant" not in picked:
        variant_pick = engine_idx[0] if engine_idx else None
        if variant_pick is None:
            leftover = [i for i in plain_idx if not used[i]]
            variant_pick = leftover[0] if leftover else None
        if variant_pick is not None and not used[variant_pick]:
            take("variant", variant_pick, _clean_text(cells[variant_pick]), "high" if variant_pick in engine_idx else "low", "engine_tokens" if variant_pick in engine_idx else "position")

    # Anything still unused and textual becomes an informational note.
    notes = [_clean_text(cells[i]) for i in range(n) if not used[i] and (_clean_text(cells[i]) or "")]

    # Assemble a normalizer-style row dict for the shared shaper.
    row = {
        "brand": picked.get("brand", (None, None, None, None))[1],
        "model": picked.get("model", (None, None, None, None))[1],
        "variant": picked.get("variant", (None, None, None, None))[1],
        "year": picked.get("year", (None, None, None, None))[1],
        "fuel": picked.get("fuel", (None, None, None, None))[1],
        "km": picked.get("km", (None, None, None, None))[1],
        "price": picked.get("price", (None, None, None, None))[1],
        "body_type": picked.get("body_type", (None, None, None, None))[1],
        "vin": picked.get("vin", (None, None, None, None))[1],
        "colour": picked.get("colour", (None, None, None, None))[1],
    }
    confidences = {f: picked[f][2] for f in picked}
    sources = {f: picked[f][3] for f in picked}
    extra_issues = []
    if notes:
        extra_issues.append(f"Unassigned text ignored: {', '.join(notes)}")

    # Flag SOLD / non-numeric price explicitly.
    price_cells = [cells[picked[f][0]] for f in ("price",) if f in picked]
    for pc in price_cells:
        _, status, _ = norm_price(pc)
        if status in ("sold", "reserved", "unavailable"):
            extra_issues.append(f"Price reads {status.upper()} — excluded from valuation; enter a number to compare.")

    return _shape(
        row, source="content", mode="headerless", cells=cells,
        extra_issues=extra_issues, confidences=confidences, sources=sources,
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# Shared result shaping (used by both header and headerless paths)
# --------------------------------------------------------------------------- #
def _shape(
    row: dict,
    source: str,
    mode: str,
    cells: list,
    extra_issues: Optional[list] = None,
    confidences: Optional[dict] = None,
    sources: Optional[dict] = None,
    notes: Optional[list] = None,
) -> ParsedRow:
    confidences = confidences or {}
    sources = sources or {}
    issues = list(extra_issues or [])

    def val(k):
        v = row.get(k)
        if isinstance(v, float) and pd.isna(v):
            return None
        return v

    car: dict = {}
    fields: dict = {}
    for f in CAR_FIELDS:
        v = val(f)
        detected = v is not None and v != ""
        car[f] = v if detected else None
        fields[f] = FieldDetection(
            value=car[f],
            detected=detected,
            confidence=confidences.get(f, "high" if detected else "none"),
            source=sources.get(f, source if detected else "none"),
        )

    extras = {
        "vin": val("vin"),
        "colour": val("colour"),
        "notes": notes or [],
    }
    not_detected = [f for f in RECOMMENDED_FIELDS if not car.get(f)]
    ok = bool(car.get("brand") and car.get("model"))
    if not ok and "brand and model are required" not in " ".join(issues).lower():
        issues.append("Could not detect brand and model — the two required fields. Please fill them in.")

    return ParsedRow(
        ok=ok, mode=mode, car=car, fields=fields, extras=extras,
        cells=list(cells), issues=issues, not_detected=not_detected,
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def parse_pasted_row(text: str) -> ParsedRow:
    """Parse a single pasted table row into canonical single-car fields."""
    lines = _clean_lines(text)
    if not lines:
        return ParsedRow(
            ok=False, mode="empty", car={f: None for f in CAR_FIELDS}, fields={},
            extras={}, cells=[], issues=["Nothing to parse — paste a table row."],
            not_detected=list(RECOMMENDED_FIELDS),
        )

    # Header + data row.
    if len(lines) >= 2:
        head = _split_cells(lines[0], drop_empty=False)
        if _looks_like_header(head):
            data = _split_cells(lines[1], drop_empty=False)
            return _parse_with_header(head, data)

    # A single line that is ONLY a header.
    single_nodrop = _split_cells(lines[0], drop_empty=False)
    if len(lines) == 1 and _looks_like_header(single_nodrop):
        return ParsedRow(
            ok=False, mode="header_only",
            car={f: None for f in CAR_FIELDS}, fields={}, extras={},
            cells=single_nodrop,
            issues=["That looks like just the header row. Paste a data row — the header is optional."],
            not_detected=list(RECOMMENDED_FIELDS),
        )

    # Headerless data row (the common case).
    return _parse_headerless(_split_cells(lines[0], drop_empty=True))
