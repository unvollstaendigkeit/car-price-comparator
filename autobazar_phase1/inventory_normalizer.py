"""
Inventory input-normalization layer.

Sits BEFORE the market-comparison engine and is deliberately independent of it:
it turns an arbitrary dealer spreadsheet (any reasonable column naming) into a
single canonical schema. The same canonical record is reused by both the
bulk inventory-upload path and the single-car manual-entry path.

Design principles (carried from the rest of the project):
  * Deterministic and rule-based. We never invent a value we cannot parse.
  * Evidence over guessing. Where a header is ambiguous (e.g. a bare "Type"
    that could be fuel OR body), we disambiguate by INSPECTING THE COLUMN'S
    VALUES against known vocabularies. If the values are inconclusive we do
    NOT guess - we report the ambiguity and ask the caller to map it manually.
  * Full auditability. Every transformed field keeps its original string, and
    a price of "SOLD" becomes price=None + status="sold" (a valid record that
    is simply excluded from valuation).

Public API:
    normalize_inventory(source, manual_mapping=None) -> NormalizationReport
    normalize_record(record, manual_mapping=None)     -> (dict, list[str])
    print_report(report)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Union

import pandas as pd


# --------------------------------------------------------------------------- #
# Canonical schema
# --------------------------------------------------------------------------- #
CANONICAL_FIELDS = [
    "year", "fuel", "brand", "model", "variant", "body_type",
    "vin", "colour", "km", "price", "pictures", "co2", "availability",
]

# A row must have these to be usable by the comparison engine. Price is special:
# a "SOLD"/unavailable price still makes a VALID inventory record (req. 7), it is
# just flagged and excluded from valuation rather than treated as an error.
REQUIRED_FOR_COMPARISON = ["brand", "model", "year", "fuel", "km", "price"]


# --------------------------------------------------------------------------- #
# Vocabularies (shared source of truth for values + value-based disambiguation)
# --------------------------------------------------------------------------- #
CANON_FUELS = {"Petrol", "Diesel", "Hybrid", "PHEV", "Electric", "LPG", "CNG"}

_FUEL_MAP = {
    "benzin": "Petrol", "benzín": "Petrol", "petrol": "Petrol",
    "gasoline": "Petrol", "gas": "Petrol", "essence": "Petrol",
    "diesel": "Diesel", "nafta": "Diesel", "tdi": "Diesel", "hdi": "Diesel",
    "hybrid": "Hybrid", "hev": "Hybrid", "full hybrid": "Hybrid",
    "mild hybrid": "Hybrid", "mhev": "Hybrid",
    "phev": "PHEV", "plugin": "PHEV", "plug in": "PHEV",
    "plug-in": "PHEV", "plugin-hybrid": "PHEV", "plug-in hybrid": "PHEV",
    "plug in hybrid": "PHEV",
    "electric": "Electric", "elektro": "Electric", "el": "Electric",
    "ev": "Electric", "bev": "Electric", "elbil": "Electric",
    "lpg": "LPG", "cng": "CNG",
}

_BODY_MAP = {
    "suv": "SUV", "stationcar": "Estate", "estate": "Estate",
    "hatchback": "Hatchback", "mpv": "MPV", "sedan": "Sedan",
    "saloon": "Sedan", "combi": "Estate", "kombi": "Estate",
    "wagon": "Estate", "coupe": "Coupe", "coupé": "Coupe",
    "cabriolet": "Cabriolet", "convertible": "Cabriolet",
    "van": "Van", "pickup": "Pickup", "liftback": "Liftback",
    "roadster": "Roadster", "minivan": "MPV",
}
_BODY_VOCAB = set(_BODY_MAP.keys())

_HP_TO_KW = 0.7355  # metric HP (PS/HK) -> kW; used elsewhere, exported for reuse


# --------------------------------------------------------------------------- #
# Header synonym dictionary. Tokens are stored already header-normalized
# (lowercase, punctuation->space, collapsed). Extend freely.
#
# NOTE: the token "type" appears under BOTH fuel and body_type on purpose - it
# is genuinely ambiguous and is resolved later by inspecting column values.
# --------------------------------------------------------------------------- #
SYNONYMS: dict[str, set[str]] = {
    "year": {
        "year", "registratio", "registration", "registration date",
        "registration year", "reg", "reg date", "reg year", "model year",
        "first registration", "argang", "aargang",
    },
    "fuel": {
        "fuel", "fuel type", "type", "drivmiddel", "braendstof",
        "engine type", "propulsion",
    },
    "brand": {"brand", "make", "marke", "maerke", "manufacturer", "brand name"},
    "model": {"model", "modell", "model name", "modell name"},
    "variant": {
        "variant", "version", "trim", "variant engine", "engine",
        "variant version", "equipment", "spec",
    },
    "body_type": {
        "body", "body type", "bodytype", "karoseri", "karoseria", "kar",
        "coachwork", "chassis type", "type", "form", "coach", "shape",
    },
    "vin": {
        "vin", "chassis number", "chassis no", "chassis", "fin",
        "vehicle identification number", "chassisnr",
    },
    "colour": {
        "colour", "color", "farve", "farba", "paint",
        "exterior colour", "exterior color",
    },
    "km": {
        "km", "mileage", "odometer", "kilometers", "kilometres", "kilometer",
        "kilometerstand", "kmstand", "km stand", "mileage km", "kilometraz",
        "odo",
    },
    "price": {
        "price", "price full vat", "price incl vat", "price including vat",
        "price eur", "retail price", "asking price", "sales price", "pris",
        "price full", "full price", "price vat", "list price",
    },
    "pictures": {
        "pictures", "picture", "photos", "photo", "images", "image", "foto",
        "fotos", "link", "links", "url", "gallery", "media",
    },
    "co2": {
        "co2", "co2 emissions", "co2 emission", "emissions", "co2 gkm",
        "co2 g km", "emission",
    },
    "availability": {
        "good to go", "availability", "status", "available", "ready",
        "stock status",
    },
}

# Reverse index: normalized token -> set of canonical fields it could mean.
_TOKEN_TO_CANON: dict[str, set[str]] = {}
for _canon, _toks in SYNONYMS.items():
    for _t in _toks:
        _TOKEN_TO_CANON.setdefault(_t, set()).add(_canon)


# --------------------------------------------------------------------------- #
# Small value helpers
# --------------------------------------------------------------------------- #
def _norm_header(h: str) -> str:
    s = str(h).strip().lower()
    s = re.sub(r"[._/\\]+", " ", s)   # dots/slashes -> space ("Kar." -> "kar")
    s = re.sub(r"[^\w\s]", "", s)     # drop remaining punctuation "()%"
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _clean_text(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _to_int(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return None if pd.isna(value) else int(value)
    text = str(value).strip()
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def norm_fuel(raw) -> Optional[str]:
    """Map a raw fuel string to a canonical fuel category (or None)."""
    t = _clean_text(raw)
    if not t:
        return None
    return _FUEL_MAP.get(t.lower())


def norm_body(raw) -> Optional[str]:
    t = _clean_text(raw)
    if not t:
        return None
    return _BODY_MAP.get(t.lower(), t)


def norm_year(raw) -> tuple[Optional[int], str]:
    """Parse a year from a bare year or a registration date. Returns (year, provenance)."""
    t = _clean_text(raw)
    if not t:
        return None, "missing"
    now = datetime.now().year
    if re.fullmatch(r"(19|20)\d{2}", t):
        y = int(t)
        if 1990 <= y <= now + 1:
            return y, "bare_year"
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%m/%d/%y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(t, fmt)
            if 1990 <= dt.year <= now + 1:
                return dt.year, f"date[{fmt}]"
        except ValueError:
            continue
    m = re.search(r"\b(19|20)\d{2}\b", t)
    if m:
        return int(m.group(0)), "year_regex"
    return None, "unparsed"


_UNAVAILABLE_PRICE_RE = re.compile(
    r"\b(sold|solgt|sold out|reserved|reserveret|tbd|poa|"
    r"price on request|unavailable|not for sale|pending|n\s*[/.]?\s*a)\b",
    re.I,
)


def norm_price(raw) -> tuple[Optional[int], str, Optional[str]]:
    """
    Returns (price_int_or_None, status, original_string).
    status is one of: 'active', 'sold', 'reserved', 'unavailable', 'missing',
    'unparsed'. A non-numeric availability token (SOLD, Reserved, ...) yields a
    valid record with price=None - it is excluded from valuation, not rejected.
    """
    original = _clean_text(raw)
    if original is None:
        return None, "missing", None
    m = _UNAVAILABLE_PRICE_RE.search(original)
    if m:
        tok = re.sub(r"[^a-z]", "", m.group(1).lower())
        if tok in ("sold", "solgt", "soldout"):
            return None, "sold", original
        if tok in ("reserved", "reserveret", "pending"):
            return None, "reserved", original
        return None, "unavailable", original
    value = _to_int(original)
    if value is None:
        return None, "unparsed", original
    return value, "active", original


# --------------------------------------------------------------------------- #
# Value-based disambiguation for ambiguous headers (the "Type"/"type" case)
# --------------------------------------------------------------------------- #
def _vocab_score(series: pd.Series, kind: str) -> float:
    """Fraction of non-empty values that look like a fuel / body vocabulary term."""
    vals = [v for v in (_clean_text(x) for x in series.tolist()) if v]
    if not vals:
        return 0.0
    if kind == "fuel":
        hits = sum(1 for v in vals if v.lower() in _FUEL_MAP)
    else:  # body
        hits = sum(1 for v in vals if v.lower() in _BODY_VOCAB)
    return hits / len(vals)


# --------------------------------------------------------------------------- #
# Report object
# --------------------------------------------------------------------------- #
@dataclass
class NormalizationReport:
    detected_columns: list[str]
    mapping: dict[str, str]                 # canonical -> source header
    unmapped_columns: list[str]
    missing_required: list[str]
    ambiguities: list[str]                  # human-readable, need manual mapping
    rows: pd.DataFrame                       # canonical + *_original + status
    row_issues: list[dict]                   # [{row_index, issues:[...]}]
    counts: dict[str, int]

    @property
    def ok(self) -> bool:
        """True when there is nothing that blocks processing."""
        return not self.ambiguities and not self.missing_required


# --------------------------------------------------------------------------- #
# Column mapping
# --------------------------------------------------------------------------- #
def map_columns(
    df: pd.DataFrame,
    manual_mapping: Optional[dict[str, str]] = None,
) -> tuple[dict[str, str], list[str], list[str]]:
    """
    Resolve source headers to canonical fields.

    Returns (mapping canonical->header, unmapped_headers, ambiguity_messages).
    """
    # Two-tier manual override: an EXACT (case-sensitive) header map takes
    # precedence, so columns that differ only by case (e.g. 'Type' -> fuel vs
    # 'type' -> body_type) can each be pinned individually. A normalized map is
    # kept as a convenience fallback for the common single-column case.
    manual_exact = dict(manual_mapping or {})
    manual_norm = {_norm_header(k): v for k, v in (manual_mapping or {}).items()}
    headers = list(df.columns)

    assigned: dict[str, str] = {}          # canonical -> header
    unmapped: list[str] = []
    ambiguous_headers: list[tuple[str, set[str]]] = []
    conflicts: dict[str, list[str]] = {}   # canonical -> [headers] competing

    def assign(canon: str, header: str):
        if canon in assigned and assigned[canon] != header:
            conflicts.setdefault(canon, [assigned[canon]]).append(header)
        else:
            assigned[canon] = header

    for header in headers:
        norm = _norm_header(header)

        # 1) manual override wins outright (exact header first, then normalized).
        #    The normalized fallback is skipped when any exact key targets a
        #    header sharing this normalized form, so case-only-different columns
        #    are never cross-assigned.
        if header in manual_exact:
            assign(manual_exact[header], header)
            continue
        exact_claims_this_norm = any(_norm_header(k) == norm for k in manual_exact)
        if norm in manual_norm and not exact_claims_this_norm:
            assign(manual_norm[norm], header)
            continue

        canon_set = _TOKEN_TO_CANON.get(norm)
        if not canon_set:
            unmapped.append(header)
        elif len(canon_set) == 1:
            assign(next(iter(canon_set)), header)
        else:
            # genuinely ambiguous token (e.g. "type"): resolve later by values
            ambiguous_headers.append((header, set(canon_set)))

    ambiguity_msgs: list[str] = []

    # 2) resolve ambiguous headers by inspecting their values
    for header, canon_set in ambiguous_headers:
        scores = {}
        if "fuel" in canon_set:
            scores["fuel"] = _vocab_score(df[header], "fuel")
        if "body_type" in canon_set:
            scores["body_type"] = _vocab_score(df[header], "body")
        # prefer a field not already firmly taken, then the higher score
        ranked = sorted(
            scores.items(),
            key=lambda kv: (kv[1], kv[0] not in assigned),
            reverse=True,
        )
        best, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        if best_score >= 0.5 and best_score > second_score:
            assign(best, header)
        else:
            ambiguity_msgs.append(
                f"Column {header!r} is ambiguous between {sorted(canon_set)} "
                f"(value match scores { {k: round(v,2) for k,v in scores.items()} }). "
                f"Map it manually, e.g. manual_mapping={{'{header}': 'fuel'}}."
            )

    # 3) report conflicts (several headers claim the same field)
    for canon, hdrs in conflicts.items():
        ambiguity_msgs.append(
            f"Multiple columns map to '{canon}': {hdrs}. "
            f"Keep one via manual_mapping (map the others to their real field)."
        )

    return assigned, unmapped, ambiguity_msgs


# --------------------------------------------------------------------------- #
# Row normalization
# --------------------------------------------------------------------------- #
def _normalize_rows(df: pd.DataFrame, mapping: dict[str, str]) -> tuple[pd.DataFrame, list[dict]]:
    def col(canon):
        h = mapping.get(canon)
        return df[h] if h is not None else pd.Series([None] * len(df))

    out_rows: list[dict] = []
    issues_all: list[dict] = []

    src = {c: col(c) for c in CANONICAL_FIELDS}

    for i in range(len(df)):
        def gv(c):
            return src[c].iloc[i] if mapping.get(c) is not None else None

        year, ysrc = norm_year(gv("year"))
        fuel_raw = _clean_text(gv("fuel"))
        fuel = norm_fuel(fuel_raw)
        km = _to_int(gv("km"))
        price, status, price_orig = norm_price(gv("price"))
        co2 = _to_int(gv("co2"))
        brand = _clean_text(gv("brand"))
        model = _clean_text(gv("model"))
        variant = _clean_text(gv("variant"))
        body_raw = _clean_text(gv("body_type"))
        body = norm_body(body_raw)

        rec = {
            "row_index": i,
            "brand": brand,
            "model": model,
            "variant": variant,
            "fuel": fuel,
            "fuel_original": fuel_raw,
            "year": year,
            "year_source": ysrc,
            "body_type": body,
            "body_type_original": body_raw,
            "vin": _clean_text(gv("vin")),
            "colour": _clean_text(gv("colour")),
            "km": km,
            "km_original": _clean_text(gv("km")),
            "price": price,
            "price_original": price_orig,
            "status": status,
            "co2": co2,
            "co2_original": _clean_text(gv("co2")),
            "pictures": _clean_text(gv("pictures")),
            "availability": _clean_text(gv("availability")),
        }

        # per-row validation
        issues: list[str] = []
        if not brand:
            issues.append("missing brand")
        if not model:
            issues.append("missing model")
        if year is None:
            issues.append(f"invalid/missing year ({_clean_text(gv('year'))!r})")
        if fuel is None:
            if fuel_raw:
                issues.append(f"unrecognized fuel ({fuel_raw!r})")
            else:
                issues.append("missing fuel")
        if km is None:
            issues.append("missing km")

        if status in ("sold", "reserved", "unavailable"):
            issues.append(f"price {status}: excluded from valuation")
            valuable = False
        elif price is None:
            issues.append(f"invalid/missing price ({price_orig!r})")
            valuable = False
        else:
            valuable = True

        # valid for comparison = all required present AND a usable numeric price
        blocking = [
            x for x in issues
            if not x.endswith("excluded from valuation")
        ]
        rec["valid_for_comparison"] = (len(blocking) == 0) and valuable
        rec["issues"] = "; ".join(issues)
        out_rows.append(rec)
        if issues:
            issues_all.append({"row_index": i, "issues": issues})

    return pd.DataFrame(out_rows), issues_all


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def normalize_inventory(
    source: Union[str, pd.DataFrame],
    manual_mapping: Optional[dict[str, str]] = None,
) -> NormalizationReport:
    """
    Normalize a dealer inventory (CSV path or DataFrame) into the canonical
    schema, returning a full validation report. Does not raise on messy data;
    problems are surfaced in the report instead.
    """
    if isinstance(source, str):
        df = pd.read_csv(source, dtype=str)
    else:
        df = source.copy()
    df.columns = [str(c).strip() for c in df.columns]

    mapping, unmapped, ambiguities = map_columns(df, manual_mapping)
    missing_required = [c for c in REQUIRED_FOR_COMPARISON if c not in mapping]

    rows, row_issues = _normalize_rows(df, mapping)

    total = len(rows)
    sold = int((rows["status"].isin(["sold", "reserved", "unavailable"])).sum()) if total else 0
    valid = int(rows["valid_for_comparison"].sum()) if total else 0
    invalid = total - valid - sold if total else 0
    counts = {
        "total_rows": total,
        "valid_for_comparison": valid,
        "sold_or_unavailable": sold,
        "invalid": max(invalid, 0),
    }

    return NormalizationReport(
        detected_columns=list(df.columns),
        mapping=mapping,
        unmapped_columns=unmapped,
        missing_required=missing_required,
        ambiguities=ambiguities,
        rows=rows,
        row_issues=row_issues,
        counts=counts,
    )


def normalize_record(
    record: dict,
    manual_mapping: Optional[dict[str, str]] = None,
) -> tuple[dict, list[str]]:
    """
    Normalize a SINGLE record (single-car manual-entry mode). `record` is a dict
    keyed by arbitrary headers. Returns (canonical_dict, issues).
    """
    df = pd.DataFrame([record])
    report = normalize_inventory(df, manual_mapping)
    row = report.rows.iloc[0].to_dict()
    issues = list(report.ambiguities)
    if report.missing_required:
        issues.append(f"missing required fields: {report.missing_required}")
    if report.row_issues:
        issues.extend(report.row_issues[0]["issues"])
    return row, issues


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_report(report: NormalizationReport, title: str = "") -> None:
    line = "=" * 68
    print(line)
    print(f"IMPORT VALIDATION REPORT{(' - ' + title) if title else ''}")
    print(line)

    print(f"\nDetected columns ({len(report.detected_columns)}):")
    print("  " + ", ".join(repr(c) for c in report.detected_columns))

    print("\nMapped canonical fields:")
    for canon in CANONICAL_FIELDS:
        if canon in report.mapping:
            print(f"  {canon:12} <- {report.mapping[canon]!r}")

    print("\nUnmapped columns:")
    print("  " + (", ".join(repr(c) for c in report.unmapped_columns) or "(none)"))

    print("\nMissing required fields:")
    print("  " + (", ".join(report.missing_required) or "(none)"))

    print("\nAmbiguities needing manual mapping:")
    if report.ambiguities:
        for a in report.ambiguities:
            print(f"  - {a}")
    else:
        print("  (none)")

    print("\nRow validation issues:")
    if report.row_issues:
        for ri in report.row_issues:
            print(f"  row {ri['row_index']}: {'; '.join(ri['issues'])}")
    else:
        print("  (none)")

    c = report.counts
    print("\nSummary counts:")
    print(f"  total rows           : {c['total_rows']}")
    print(f"  valid for comparison : {c['valid_for_comparison']}")
    print(f"  sold / unavailable   : {c['sold_or_unavailable']}")
    print(f"  invalid              : {c['invalid']}")
    print(line + "\n")


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "inventory_sample.csv"
    rep = normalize_inventory(src)
    print_report(rep, title=src)
