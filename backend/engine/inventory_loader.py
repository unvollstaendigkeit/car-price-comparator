"""
Phase 6 - Inventory loader.

Reads the dealer inventory (Excel exported to CSV) and normalizes every row into
the SAME canonical schema used by the market-side scrapers, so an inventory car
and a market listing can be compared field-for-field.

Design principles carried over from earlier phases:
  * Deterministic and rule-based. No guessing: a field we cannot parse is left
    as None, never invented.
  * Every derived value records HOW it was obtained (provenance), so the report
    can distinguish a real value from an absence.
  * Robust to messy, real-world, multi-locale text. This particular sheet is a
    Danish-market export, so we must handle:
      - power expressed in HP / HK / hk (hestekrafter) as well as PS / kW
      - decimal COMMA displacement ("1,2 TSI")
      - Danish fuel word "Benzin" (= Petrol)
      - mixed-case body types ("suv" / "SUV" / "Suv")
      - US-style registration dates (M/D/YYYY)
      - thousands-separated Km / Price ("122,000")

IMPORTANT CURRENCY CAVEAT
-------------------------
The Price column has no currency marker. Values (~6,000-17,000 for late-model
cars) are consistent with EUR trade figures, so we treat them as EUR to allow a
comparison with the Slovak (EUR) market. This is an ASSUMPTION, surfaced on
every row via `price_currency_assumed` and in the report. If the sheet is in DKK
the undervaluation numbers are meaningless and must be re-based first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Optional

import pandas as pd

from inventory_normalizer import normalize_inventory


# --------------------------------------------------------------------------- #
# Canonical fuel vocabulary (shared with the market normalizer).
# --------------------------------------------------------------------------- #
CANON_FUELS = {"Petrol", "Diesel", "Hybrid", "PHEV", "Electric", "LPG", "CNG"}

_FUEL_MAP = {
    "benzin": "Petrol",
    "benzín": "Petrol",
    "petrol": "Petrol",
    "gasoline": "Petrol",
    "diesel": "Diesel",
    "nafta": "Diesel",
    "hybrid": "Hybrid",
    "phev": "PHEV",
    "plugin-hybrid": "PHEV",
    "plug-in hybrid": "PHEV",
    "electric": "Electric",
    "elektro": "Electric",
    "el": "Electric",
    "lpg": "LPG",
    "cng": "CNG",
}

# Metric horsepower (PS = HK = metric HP) -> kW. European/Danish "HP" is metric.
_HP_TO_KW = 0.7355


def _cell(row, key: str) -> Optional[str]:
    """Read a cell as a clean string; NaN / empty -> None. Never raises."""
    val = row.get(key)
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    text = str(val).strip()
    return text or None


def _clean_int(value) -> Optional[int]:
    """'122,000' / '11 600' / ' 15950 ' -> int. None-safe. Never raises."""
    if value is None:
        return None
    if isinstance(value, (int,)):
        return int(value)
    if isinstance(value, float):
        return None if pd.isna(value) else int(value)
    text = str(value).strip()
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)  # drop , . spaces € kr etc.
    return int(digits) if digits else None


def _norm_fuel(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    key = str(raw).strip().lower()
    return _FUEL_MAP.get(key)


def _norm_body(raw: Optional[str]) -> Optional[str]:
    if not raw or pd.isna(raw):
        return None
    t = str(raw).strip().lower()
    mapping = {
        "suv": "SUV",
        "stationcar": "Estate",
        "estate": "Estate",
        "hatchback": "Hatchback",
        "mpv": "MPV",
        "sedan": "Sedan",
        "combi": "Estate",
        "kombi": "Estate",
    }
    return mapping.get(t, str(raw).strip())


def _year_from_registration(raw: Optional[str]) -> tuple[Optional[int], str]:
    """Registration date -> model year. Tries several formats; returns (year, provenance)."""
    if not raw or pd.isna(raw):
        return None, "missing"
    text = str(raw).strip()
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            dt = datetime.strptime(text, fmt)
            if 1990 <= dt.year <= datetime.now().year + 1:
                return dt.year, f"registration_date[{fmt}]"
        except ValueError:
            continue
    # last resort: a bare 4-digit year embedded in the string
    m = re.search(r"\b(19|20)\d{2}\b", text)
    if m:
        return int(m.group(0)), "registration_year_regex"
    return None, "unparsed"


# --------------------------------------------------------------------------- #
# Variant parsing: displacement, power, transmission, engine badge, trim.
# --------------------------------------------------------------------------- #

# Displacement at (or near) the start: "1.0", "2,0", "1,4 TSI"
_DISP_RE = re.compile(r"\b(\d)[.,](\d)\b")

# Power: number + unit. Units seen: HP, HK, hk, PS, kW/kw.
# We deliberately anchor to a unit token so we never grab a random number
# (trim numbers like "H3", "18i", "2008" must NOT be read as power).
_POWER_RE = re.compile(r"(\d{2,3})\s*(kW|kw|HK|hk|HP|hp|PS|ps)\b")

# Engine badge families (petrol/diesel marketing codes).
_BADGE_RE = re.compile(
    r"\b(TDI|TSI|TFSI|CDTI|CRDI|HDI|DCI|SKYACTIV-?[GD]?|VVT-?I|E-?TECH|GTE|MHEV|TCe|VTEC)\b",
    re.I,
)

_TRANS_RULES = [
    ("Automatic", re.compile(r"\b(automatic|automat|aut\.?|dsg|s-?tronic|tiptronic|cvt|edc|dct)\b", re.I)),
    ("Manual", re.compile(r"\b(manual|man\.?|\d\s?g\b|\d-?gear)\b", re.I)),
]

# Drivetrain: only a POSITIVE textual signal is trusted (2026-08-28 business
# sign-off) -- absence of any marker means "unknown", never inferred as FWD by
# default, since most trims don't state their drivetrain at all and guessing
# would violate the "never invent a value we cannot parse" principle.
_DRIVETRAIN_RULES = [
    ("AWD", re.compile(r"\b(4x4|4motion|quattro|xdrive|x-drive|awd|allrad|4matic|syncro|4wd)\b", re.I)),
    ("RWD", re.compile(r"\b(rwd|zadn[yý]\s*n[aá]hon|zadok[oó]lka)\b", re.I)),
    ("FWD", re.compile(r"\b(fwd|2wd|predn[yý]\s*n[aá]hon|predok[oó]lka)\b", re.I)),
]


def _parse_displacement(variant: str) -> Optional[float]:
    m = _DISP_RE.search(variant)
    if not m:
        return None
    val = float(f"{m.group(1)}.{m.group(2)}")
    return val if 0.6 <= val <= 8.0 else None


def _parse_power_kw(variant: str) -> tuple[Optional[int], str]:
    m = _POWER_RE.search(variant)
    if not m:
        return None, "missing"
    num = int(m.group(1))
    unit = m.group(2).lower()
    if unit in ("kw",):
        return num, "variant_kW"
    # HP / HK / PS -> metric hp -> kW
    kw = round(num * _HP_TO_KW)
    return kw, f"variant_{m.group(2)}->kW"


def _parse_transmission(variant: str) -> Optional[str]:
    for label, rx in _TRANS_RULES:
        if rx.search(variant):
            return label
    return None


def _parse_drivetrain(variant: str) -> Optional[str]:
    for label, rx in _DRIVETRAIN_RULES:
        if rx.search(variant):
            return label
    return None


def _parse_engine_badge(variant: str, disp: Optional[float]) -> Optional[str]:
    """
    Engine identity string. Prefer '<disp> <BADGE>' (e.g. '2.0 TDI'); when no
    marketing badge is present (common for petrol trims like '1.0 119HP ...'),
    fall back to the displacement alone ('1.0'). Displacement is always kept to
    one decimal so it groups cleanly in matching.
    """
    disp_str = f"{disp:.1f}" if disp else None
    m = _BADGE_RE.search(variant)
    if not m:
        return disp_str
    badge = m.group(1).upper()
    return f"{disp_str} {badge}" if disp_str else badge


@dataclass
class InventoryCar:
    row_index: int
    brand: Optional[str]
    model: Optional[str]
    variant_raw: Optional[str]
    variant_engine: Optional[str]
    displacement_l: Optional[float]
    fuel: Optional[str]
    fuel_raw: Optional[str]
    year: Optional[int]
    power_kw: Optional[int]
    transmission: Optional[str]
    drivetrain: Optional[str]
    body_type: Optional[str]
    km: Optional[int]
    price: Optional[int]
    price_currency_assumed: str
    vin: Optional[str]
    colour: Optional[str]
    availability: Optional[str]
    # provenance
    year_source: str = ""
    power_source: str = ""

    def as_row(self) -> dict:
        return asdict(self)


def load_inventory(path: str, assumed_currency: str = "EUR") -> pd.DataFrame:
    """
    Load an inventory CSV into the canonical normalized schema used by the
    comparison engine.

    Header detection and value normalization (km/price/year/fuel/body, SOLD
    handling, synonym mapping across differing column layouts) are delegated to
    the standalone `inventory_normalizer` layer, so this loader works on ANY
    reasonable column naming - not just the original Danish export. On top of
    the canonical fields it derives the engine-specific attributes the matcher
    needs (displacement, power in kW, transmission, engine badge) from the
    free-text variant string.
    """
    report = normalize_inventory(path)

    cars: list[dict] = []
    for _, r in report.rows.iterrows():
        brand = _cell(r, "brand")
        model = _cell(r, "model")
        if not (brand and model):
            continue  # skip blank / separator rows

        variant = _cell(r, "variant") or ""
        disp = _parse_displacement(variant) if variant else None
        power, psrc = _parse_power_kw(variant) if variant else (None, "missing")
        trans = _parse_transmission(variant) if variant else None
        drivetrain = _parse_drivetrain(variant) if variant else None
        engine = _parse_engine_badge(variant, disp) if variant else None

        year = int(r["year"]) if pd.notna(r["year"]) else None
        km = int(r["km"]) if pd.notna(r["km"]) else None
        price = int(r["price"]) if pd.notna(r["price"]) else None

        # Preserve SOLD/unavailable provenance for auditability: fold the price
        # status into availability when the price is not a live number.
        availability = _cell(r, "availability")
        if r.get("status") and r["status"] != "active":
            tag = f"price_{r['status']}"
            availability = f"{availability} | {tag}" if availability else tag

        cars.append(
            InventoryCar(
                row_index=int(r["row_index"]),
                brand=brand,
                model=model,
                variant_raw=variant or None,
                variant_engine=engine,
                displacement_l=disp,
                fuel=_cell(r, "fuel"),
                fuel_raw=_cell(r, "fuel_original"),
                year=year,
                power_kw=power,
                transmission=trans,
                drivetrain=drivetrain,
                body_type=_cell(r, "body_type"),
                km=km,
                price=price,
                price_currency_assumed=assumed_currency,
                vin=_cell(r, "vin"),
                colour=_cell(r, "colour"),
                availability=availability,
                year_source=_cell(r, "year_source") or "",
                power_source=psrc,
            ).as_row()
        )

    return pd.DataFrame(cars)


def coverage_report(df: pd.DataFrame) -> None:
    n = len(df)
    print(f"Inventory rows loaded: {n}")
    print("\nField coverage (non-null):")
    for col in [
        "brand", "model", "variant_engine", "displacement_l", "fuel",
        "year", "power_kw", "transmission", "body_type", "km", "price", "vin",
    ]:
        nn = df[col].notna().sum()
        print(f"  {col:16}: {nn}/{n} ({100*nn/n:.0f}%)")
    print("\nFuel distribution (normalized):")
    print("  ", df["fuel"].value_counts(dropna=False).to_dict())
    print("Power source breakdown:")
    print("  ", df["power_source"].value_counts(dropna=False).to_dict())
    unmapped = df[df["fuel"].isna() & df["fuel_raw"].notna()]["fuel_raw"].unique()
    if len(unmapped):
        print("UNMAPPED fuel values:", list(unmapped))


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "inventory_sample.csv"
    df = load_inventory(src)
    coverage_report(df)
    out = "inventory_normalized.csv"
    df.to_csv(out, index=False)
    print(f"\nSaved -> {out}")
    print("\nSample (first 8):")
    cols = ["brand", "model", "year", "fuel", "variant_engine", "power_kw",
            "power_source", "transmission", "body_type", "km", "price"]
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(df[cols].head(8).to_string())
