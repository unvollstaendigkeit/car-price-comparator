"""
Phase 5 - Transparent market-comparison prototype (Skoda Kodiaq test car).

This is NOT a valuation model and uses NO AI. Every include/exclude decision is
a deterministic, field-level rule that can be printed and audited.

Pipeline:
    1. Load the already-retrieved Autobazar + Bazos candidate CSVs.
    2. Normalize both into ONE common row schema (per-field provenance kept),
       but the two sources are NEVER merged into a single dataset.
    3. Apply two explicit rule sets (STRICT, BROAD) to each source separately.
    4. Produce 4 groups: AB-strict, AB-broad, BZ-strict, BZ-broad.
    5. Report counts, unknown-field counts, matched listings, price stats, and
       price_difference vs the market median.

Missing-data policy (important):
    A rule can return MATCH / MISMATCH / UNKNOWN. A listing only FAILS a rule
    when the rule returns MISMATCH (a *known* contradiction). UNKNOWN never fails
    on its own - it passes through and is reported separately.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from statistics import mean
from typing import Optional

import pandas as pd

# --------------------------------------------------------------------------- #
# Rule outcomes
# --------------------------------------------------------------------------- #
MATCH = "match"
MISMATCH = "mismatch"
UNKNOWN = "unknown"
NA = "n/a"  # rule not applicable (e.g. inventory value itself is unknown)

# The normalized fields the frontend will eventually display per listing.
DISPLAY_FIELDS = [
    "source", "listing_id", "title", "year", "km", "variant_engine",
    "power_kw", "transmission", "fuel", "body_type", "price", "url",
]


# --------------------------------------------------------------------------- #
# Inventory car (the thing we compare the market against)
# --------------------------------------------------------------------------- #
@dataclass
class InventoryCar:
    brand: str
    model: str
    variant_engine: Optional[str]
    fuel: Optional[str]
    year: Optional[int]
    km: Optional[int]
    price: Optional[int]
    power_kw: Optional[int] = None
    transmission: Optional[str] = None
    body_type: Optional[str] = None


# --------------------------------------------------------------------------- #
# Rule set - explicit, printable tolerances
# --------------------------------------------------------------------------- #
@dataclass
class RuleSet:
    name: str
    year_tol: int                 # +/- years
    km_pct: float                 # +/- fraction of inventory km
    km_floor: int                 # minimum +/- absolute km window
    power_tol_kw: Optional[int]   # +/- kW when both known; None = ignore power
    require_same_variant: bool    # engine/variant must match (displacement+family)
    require_same_transmission: bool
    # When True, a listing with UNKNOWN year is rejected (not treated as a pass).
    # STRICT sets this: you cannot call something a tight +/-1-year comparable if
    # its year is unknown. Without it, year-less part/older listings slip into the
    # strict pool purely because "unknown never fails" and corrupt the median.
    require_known_year: bool = False

    def describe(self) -> str:
        parts = [
            f"year +/-{self.year_tol}" + (" (must be known)" if self.require_known_year else ""),
            f"km +/-{int(self.km_pct*100)}% (floor +/-{self.km_floor:,})",
            ("variant must match" if self.require_same_variant else "any engine of model"),
            (f"power +/-{self.power_tol_kw}kW (both-known)" if self.power_tol_kw is not None else "power ignored"),
            ("transmission must match (both-known)" if self.require_same_transmission else "transmission ignored"),
        ]
        return "; ".join(parts)


STRICT = RuleSet(
    name="strict",
    year_tol=1,
    km_pct=0.30,
    km_floor=20_000,
    power_tol_kw=10,
    require_same_variant=True,
    require_same_transmission=True,
    require_known_year=True,
)

# MODERATE sits between STRICT and BROAD. Its tolerances are data-tuned: a
# diagnostic over the real retrieved pools showed the STRICT blockers (in order)
# are km, year, then the hard transmission requirement - NOT engine/variant. So
# MODERATE widens year/km, drops the transmission requirement, and keeps a
# meaningful power band. It still requires a KNOWN year (no year-less shells).
MODERATE = RuleSet(
    name="moderate",
    year_tol=2,
    km_pct=0.40,
    km_floor=25_000,
    power_tol_kw=20,
    require_same_variant=False,
    require_same_transmission=False,
    require_known_year=True,
)

BROAD = RuleSet(
    name="broad",
    year_tol=3,
    km_pct=0.60,
    km_floor=40_000,
    power_tol_kw=30,
    require_same_variant=False,
    require_same_transmission=False,
)


# --------------------------------------------------------------------------- #
# Normalization helpers
# --------------------------------------------------------------------------- #
_ENGINE_RE = re.compile(r"(\d\.\d)\s*(TDI|TSI|TFSI|CRDI|CDTI|DCI|HDI|CDI)", re.I)


def _clean(v):
    """None / NaN -> None; keep everything else."""
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    return v


def _int(v) -> Optional[int]:
    v = _clean(v)
    if v is None:
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def normalize_engine(text) -> Optional[str]:
    """'... 2.0 TDI SCR ...' -> '2.0 TDI'. Returns None if no engine token."""
    text = _clean(text)
    if not text:
        return None
    m = _ENGINE_RE.search(str(text))
    if not m:
        return None
    return f"{m.group(1)} {m.group(2).upper()}"


def normalize_transmission(text) -> Optional[str]:
    """Slovak/English gearbox labels -> 'Automatic' | 'Manual' | None."""
    text = _clean(text)
    if not text:
        return None
    t = str(text).lower()
    if any(k in t for k in ("automat", "dsg", "auto", "tiptronic", "s tronic", "s-tronic")):
        return "Automatic"
    if any(k in t for k in ("manual", "manuál", "manuel", "6-st. manu", "5-st. manu")):
        return "Manual"
    return None


def normalize_fuel(text) -> Optional[str]:
    """
    Canonicalize a fuel string from either source or the inventory into one of:
    Diesel, Petrol, Hybrid, PHEV, Electric, LPG, CNG (else Title-cased original).

    Keyword/substring based with deliberate PRECEDENCE, because the sources use
    Slovak and compound labels ('Elektromotor', 'Plug-in Hybrid',
    'Hybridný (benzín/elektro)'), and exact-match would misfile them:
      1. plug-in  -> PHEV   (before generic 'hybrid')
      2. hybrid   -> Hybrid (before 'elektro', since a hybrid mentions elektro)
      3. elektro/electric -> Electric
    """
    text = _clean(text)
    if not text:
        return None
    t = str(text).strip().lower()

    # 1. Plug-in hybrid first (contains both 'hybrid' and often 'elektro').
    if "plug" in t or "phev" in t or "plug-in" in t:
        return "PHEV"
    # 2. Any remaining hybrid (may read 'benzín/elektro' -> still Hybrid).
    if "hybrid" in t:
        return "Hybrid"
    # 3. Pure electric.
    if "elektro" in t or "electric" in t or t == "ev":
        return "Electric"
    # 4. Single-fuel keywords.
    if "diesel" in t or "nafta" in t:
        return "Diesel"
    if "benzín" in t or "benzin" in t or "petrol" in t:
        return "Petrol"
    if "lpg" in t:
        return "LPG"
    if "cng" in t:
        return "CNG"
    return str(text).strip().title()


def _listing_id_from_url(url) -> Optional[str]:
    url = _clean(url)
    if not url:
        return None
    # Autobazar: .../detail/<slug>/<ID>/   Bazos: .../inzerat/<ID>/...
    m = re.search(r"/detail/[^/]+/([^/]+)/?", str(url))
    if m:
        return m.group(1)
    m = re.search(r"/inzerat/(\d+)/", str(url))
    if m:
        return m.group(1)
    return None


# --------------------------------------------------------------------------- #
# Load + normalize each source into the common schema
# --------------------------------------------------------------------------- #
def load_autobazar(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "source": "autobazar",
            "listing_id": _listing_id_from_url(r.get("url")),
            "title": _clean(r.get("title")),
            "brand": "Škoda",
            "model": "Kodiaq",
            "variant_engine": normalize_engine(r.get("title")),
            "fuel": normalize_fuel(r.get("fuel")),
            "year": _int(r.get("year")),
            "km": _int(r.get("km")),
            "power_kw": _int(r.get("power")),
            "transmission": normalize_transmission(r.get("transmission")),
            "body_type": _clean(r.get("body_type")),
            "price": _int(r.get("price")),
            "url": _clean(r.get("url")),
            # provenance: AB structured fields come from the site's JSON
            "year_source": "structured", "km_source": "structured",
            "power_source": "structured", "transmission_source": "structured",
            "fuel_source": "structured", "body_type_source": "structured",
            "engine_source": "regex_title",
        })
    return pd.DataFrame(rows)


def load_bazos(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "source": "bazos",
            "listing_id": _clean(r.get("listing_id")),
            "title": _clean(r.get("title")),
            "brand": "Škoda",
            "model": "Kodiaq",
            "variant_engine": normalize_engine(r.get("engine")) or normalize_engine(r.get("title")),
            "fuel": normalize_fuel(r.get("fuel")),
            "year": _int(r.get("year")),
            "km": _int(r.get("km")),
            "power_kw": _int(r.get("power")),
            "transmission": normalize_transmission(r.get("transmission")),
            "body_type": _clean(r.get("body_type")),
            "price": _int(r.get("price")),
            "url": _clean(r.get("url")),
            "year_source": _clean(r.get("year_source")) or "missing",
            "km_source": _clean(r.get("km_source")) or "missing",
            "power_source": _clean(r.get("power_source")) or "missing",
            "transmission_source": _clean(r.get("transmission_source")) or "missing",
            "fuel_source": _clean(r.get("fuel_source")) or "missing",
            "body_type_source": _clean(r.get("body_type_source")) or "missing",
            "engine_source": _clean(r.get("engine_source")) or "missing",
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Field-level rule evaluators -> MATCH / MISMATCH / UNKNOWN / NA
# --------------------------------------------------------------------------- #
def rule_model(inv: InventoryCar, row) -> str:
    title = (str(row["title"]) if _clean(row["title"]) else "").lower()
    model = inv.model.lower()
    if not title:
        return UNKNOWN
    return MATCH if model in title else MISMATCH


def rule_fuel(inv: InventoryCar, row) -> str:
    if not inv.fuel:
        return NA
    val = _clean(row["fuel"])
    if val is None:
        return UNKNOWN
    return MATCH if str(val).lower() == inv.fuel.lower() else MISMATCH


def rule_variant(inv: InventoryCar, row, require: bool) -> str:
    if not require:
        return NA
    if not inv.variant_engine:
        return NA
    val = _clean(row["variant_engine"])
    if val is None:
        return UNKNOWN
    return MATCH if str(val).upper() == inv.variant_engine.upper() else MISMATCH


def rule_year(inv: InventoryCar, row, tol: int) -> str:
    if inv.year is None:
        return NA
    val = _int(row["year"])
    if val is None:
        return UNKNOWN
    return MATCH if abs(val - inv.year) <= tol else MISMATCH


def rule_km(inv: InventoryCar, row, pct: float, floor: int) -> str:
    if inv.km is None:
        return NA
    val = _int(row["km"])
    if val is None:
        return UNKNOWN
    window = max(int(inv.km * pct), floor)
    return MATCH if abs(val - inv.km) <= window else MISMATCH


def rule_power(inv: InventoryCar, row, tol: Optional[int]) -> str:
    if tol is None or inv.power_kw is None:
        return NA  # non-binding: ignored, or inventory power unknown
    val = _int(row["power_kw"])
    if val is None:
        return UNKNOWN
    return MATCH if abs(val - inv.power_kw) <= tol else MISMATCH


def rule_transmission(inv: InventoryCar, row, require: bool) -> str:
    if not require or not inv.transmission:
        return NA
    val = _clean(row["transmission"])
    if val is None:
        return UNKNOWN
    return MATCH if str(val).lower() == inv.transmission.lower() else MISMATCH


def evaluate(inv: InventoryCar, row, rules: RuleSet) -> tuple[bool, dict]:
    """Return (passed, per-field outcomes). Passes iff NO rule returns MISMATCH."""
    outcomes = {
        "model": rule_model(inv, row),
        "fuel": rule_fuel(inv, row),
        "variant": rule_variant(inv, row, rules.require_same_variant),
        "year": rule_year(inv, row, rules.year_tol),
        "km": rule_km(inv, row, rules.km_pct, rules.km_floor),
        "power": rule_power(inv, row, rules.power_tol_kw),
        "transmission": rule_transmission(inv, row, rules.require_same_transmission),
    }
    passed = not any(v == MISMATCH for v in outcomes.values())
    return passed, outcomes


def match_source(inv: InventoryCar, df: pd.DataFrame, rules: RuleSet) -> pd.DataFrame:
    keep_idx, all_outcomes = [], []
    for idx, row in df.iterrows():
        passed, outcomes = evaluate(inv, row, rules)
        if passed:
            keep_idx.append(idx)
            all_outcomes.append(outcomes)
    out = df.loc[keep_idx].copy().reset_index(drop=True)
    # attach the decision trail so it is auditable / displayable
    for f in ["model", "fuel", "variant", "year", "km", "power", "transmission"]:
        out[f"rule_{f}"] = [o[f] for o in all_outcomes]
    return out


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
def price_stats(df: pd.DataFrame) -> dict:
    prices = df["price"].dropna().astype(float)
    if len(prices) == 0:
        return {"count": 0}

    # Robust outlier trim: with >=4 prices, drop those outside a median-anchored
    # band [median/3, median*3]. This removes seller fat-fingers (e.g. a EUR
    # 59,000 tag on a 2006 Passat, or a EUR 1 shell) that would distort mean/p75
    # and can swing the median in smaller pools, while keeping the genuine spread
    # of a same-model/year/km comparable set. Below 4 prices the median is too
    # unstable to define an outlier, so we leave the sample untouched.
    trimmed = 0
    if len(prices) >= 4:
        med0 = float(prices.median())
        lo, hi = med0 / 3.0, med0 * 3.0
        kept = prices[(prices >= lo) & (prices <= hi)]
        trimmed = int(len(prices) - len(kept))
        if len(kept) >= 3:  # never trim below a usable core
            prices = kept

    return {
        "count": int(len(prices)),
        "outliers_trimmed": trimmed,
        "min": float(prices.min()),
        "p25": float(prices.quantile(0.25)),
        "median": float(prices.median()),
        "mean": float(mean(prices)),
        "p75": float(prices.quantile(0.75)),
        "max": float(prices.max()),
    }


def unknown_counts(df: pd.DataFrame) -> dict:
    fields = ["year", "km", "power_kw", "transmission", "variant_engine", "fuel", "body_type"]
    return {f: int(df[f].isna().sum()) for f in fields if f in df.columns}


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def fmt_eur(v) -> str:
    return f"EUR {v:,.0f}" if v is not None else "n/a"


def print_group(label: str, df: pd.DataFrame, inv: InventoryCar):
    stats = price_stats(df)
    print(f"\n================ {label} ================")
    print(f"count: {stats.get('count', 0)}")
    if stats.get("count", 0) == 0:
        print("  (no comparables)")
        return stats
    print(f"  min   : {fmt_eur(stats['min'])}")
    print(f"  p25   : {fmt_eur(stats['p25'])}")
    print(f"  median: {fmt_eur(stats['median'])}")
    print(f"  mean  : {fmt_eur(stats['mean'])}")
    print(f"  p75   : {fmt_eur(stats['p75'])}")
    print(f"  max   : {fmt_eur(stats['max'])}")
    if inv.price is not None:
        diff = stats["median"] - inv.price
        diff_pct = diff / stats["median"] * 100 if stats["median"] else 0.0
        print(f"  inventory price      : {fmt_eur(inv.price)}")
        print(f"  price_difference     : {fmt_eur(diff)}  (market_median - inventory)")
        print(f"  price_difference_pct : {diff_pct:+.1f}%  "
              f"(inventory is {'cheaper' if diff>0 else 'more expensive'} than market median)")
    unk = unknown_counts(df)
    print("  unknown fields among matches:",
          ", ".join(f"{k}={v}" for k, v in unk.items() if v > 0) or "none")
    return stats


def print_listings(label: str, df: pd.DataFrame, limit: int = 12):
    print(f"\n---- {label}: matched listings (showing up to {limit}) ----")
    if len(df) == 0:
        print("  (none)")
        return
    for _, r in df.head(limit).iterrows():
        yr = _int(r["year"]); km = _int(r["km"]); pw = _int(r["power_kw"])
        print(
            f"  [{r['source']}/{r['listing_id']}] "
            f"{str(r['title'])[:52]:52} | "
            f"yr={yr if yr else '?':>4} | "
            f"km={f'{km:,}' if km else '?':>8} | "
            f"{str(r['variant_engine'] or '?'):8} | "
            f"{f'{pw}kW' if pw else '?':>6} | "
            f"{str(r['transmission'] or '?'):9} | "
            f"{fmt_eur(_int(r['price']))}"
        )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--autobazar", default="autobazar_filtered_candidates.csv")
    ap.add_argument("--bazos", default="bazos_phase4_candidates.csv")
    args = ap.parse_args()

    inv = InventoryCar(
        brand="Škoda", model="Kodiaq", variant_engine="2.0 TDI",
        fuel="Diesel", year=2021, km=121_000, price=22_000,
        power_kw=None, transmission=None, body_type=None,
    )

    print("############################################################")
    print("# PHASE 5 - Transparent market comparison (deterministic)  #")
    print("############################################################")
    print("\nInventory test car:")
    print(f"  {inv.brand} {inv.model} {inv.variant_engine} | {inv.fuel} | "
          f"{inv.year} | {inv.km:,} km | {fmt_eur(inv.price)}")
    print(f"  power/transmission: UNKNOWN -> those rules are NON-BINDING for this car")
    print(f"\nSTRICT rules : {STRICT.describe()}")
    print(f"BROAD  rules : {BROAD.describe()}")

    ab = load_autobazar(args.autobazar)
    bz = load_bazos(args.bazos)

    print("\n---------------- candidate pools (before matching) ----------------")
    print(f"  Autobazar candidates: {len(ab)}")
    print(f"    unknown fields: " +
          (", ".join(f"{k}={v}" for k, v in unknown_counts(ab).items() if v > 0) or "none"))
    print(f"  Bazos candidates    : {len(bz)}")
    print(f"    unknown fields: " +
          (", ".join(f"{k}={v}" for k, v in unknown_counts(bz).items() if v > 0) or "none"))

    groups = {
        "AUTOBAZAR / STRICT": match_source(inv, ab, STRICT),
        "AUTOBAZAR / BROAD":  match_source(inv, ab, BROAD),
        "BAZOS / STRICT":     match_source(inv, bz, STRICT),
        "BAZOS / BROAD":      match_source(inv, bz, BROAD),
    }

    print("\n---------------- comparable counts ----------------")
    print(f"  Autobazar: candidates={len(ab)}  strict={len(groups['AUTOBAZAR / STRICT'])}  broad={len(groups['AUTOBAZAR / BROAD'])}")
    print(f"  Bazos    : candidates={len(bz)}  strict={len(groups['BAZOS / STRICT'])}  broad={len(groups['BAZOS / BROAD'])}")

    for label, df in groups.items():
        print_group(label, df, inv)
        print_listings(label, df)

    # persist the 4 groups separately (never merged) for the eventual UI
    for label, df in groups.items():
        fname = "phase5_" + label.lower().replace(" / ", "_") + ".csv"
        df.to_csv(fname, index=False)
    print("\nSaved 4 separate group CSVs (sources never merged).")


if __name__ == "__main__":
    main()
