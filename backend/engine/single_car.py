"""
Single-car execution path.

Runs ONE ad-hoc car through the exact same retrieval + comparison engine used
for full-inventory rows. There is no duplicated comparison logic here: the
canonical car is built into the identical `InvCar` schema and handed to
`phase7_fullrun.evaluate_car` (the shared comparison core that also backs the
full inventory run). Autobazar and Bazos are kept completely separate and are
never merged into a single market price.

Typical use:

    from single_car import SingleCarInput, compare_single_car

    result = compare_single_car(SingleCarInput(
        brand="Toyota", model="RAV4", variant="2.5 HSD 218HP Executive",
        year=2021, fuel="Hybrid", km=60000, price=32000,
    ))
    print(result["confidence"]["flag"])
    print(result["sources"]["autobazar"]["undervaluation_pct"])

`undervaluation_pct` is always defined as:

    (market_median - asking_price) / asking_price * 100

    positive  -> the car is cheaper than that source's median (below market)
    negative  -> the car is more expensive than that source's median

computed independently per source (see phase6_validate.estimate).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

# Variant parsers + canonicalizers reused verbatim from the inventory path, so a
# single car is normalized EXACTLY like an inventory row.
from inventory_loader import (
    _parse_displacement,
    _parse_engine_badge,
    _parse_power_kw,
    _parse_transmission,
)
from inventory_normalizer import _to_int, norm_body, norm_fuel

from phase5_compare import normalize_transmission
from phase6_validate import InvCar, retrieve_autobazar, retrieve_bazos
# The shared comparison core - identical engine used by the full inventory run.
from phase7_fullrun import AGREE_MAX, DISAGREE_MAX, evaluate_car


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
@dataclass
class SingleCarInput:
    """Ad-hoc car description. Mirrors the fields a single inventory row carries."""

    brand: str
    model: str
    variant: Optional[str] = None
    year: Optional[int] = None
    fuel: Optional[str] = None
    km: Optional[int] = None
    price: Optional[int] = None
    power_kw: Optional[int] = None       # optional; else derived from variant
    transmission: Optional[str] = None   # optional; else derived from variant
    body_type: Optional[str] = None      # optional


# --------------------------------------------------------------------------- #
# Canonical car construction (same schema + derivations as load_inventory)
# --------------------------------------------------------------------------- #
def build_canonical_car(inp: SingleCarInput, row_index: int = -1) -> InvCar:
    """
    Turn a `SingleCarInput` into the canonical `InvCar` the engine consumes,
    applying the SAME normalizations `load_inventory` applies to inventory rows:

      * fuel  -> canonical category via `norm_fuel` (idempotent on canonical values)
      * body  -> canonical category via `norm_body`
      * variant -> displacement / power(kW) / transmission / engine badge
      * explicit power_kw / transmission / body_type OVERRIDE the parsed values

    When power_kw / transmission are omitted they are derived from `variant`
    exactly as the inventory loader does, so a car described the two ways yields
    an identical `InvCar`.
    """
    variant = (inp.variant or "").strip()
    disp = _parse_displacement(variant) if variant else None
    parsed_power, psrc = _parse_power_kw(variant) if variant else (None, "missing")
    parsed_trans = _parse_transmission(variant) if variant else None
    engine = _parse_engine_badge(variant, disp) if variant else None

    if inp.power_kw is not None:
        power_kw, power_source = int(inp.power_kw), "explicit"
    else:
        power_kw, power_source = parsed_power, psrc

    if inp.transmission:
        transmission = normalize_transmission(inp.transmission) or parsed_trans
    else:
        transmission = parsed_trans

    body_type = norm_body(inp.body_type) if inp.body_type else None

    return InvCar(
        row_index=row_index,
        brand=str(inp.brand).strip(),
        model=str(inp.model).strip(),
        variant_engine=engine,
        fuel=norm_fuel(inp.fuel),
        year=_to_int(inp.year),
        km=_to_int(inp.km),
        price=_to_int(inp.price),
        power_kw=power_kw,
        transmission=transmission,
        body_type=body_type,
        variant_raw=variant or None,
        power_source=power_source,
    )


# --------------------------------------------------------------------------- #
# Result shaping
# --------------------------------------------------------------------------- #
_COMP_COLS = ["price", "year", "km", "url", "title",
              "fuel", "power_kw", "transmission", "body_type"]
_INT_COLS = {"price", "year", "km", "power_kw"}


def _clean_val(col: str, v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if col in _INT_COLS:
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return str(v)


def _comparables(matched: pd.DataFrame) -> list[dict]:
    """The actual chosen-tier comparable listings, each with its URL."""
    if matched is None or matched.empty:
        return []
    cols = [c for c in _COMP_COLS if c in matched.columns]
    out = []
    for _, r in matched[cols].iterrows():
        out.append({c: _clean_val(c, r[c]) for c in cols})
    return out


def _source_result(est: dict, matched: pd.DataFrame, err: str) -> dict:
    """Per-source block. Kept independent; never merged with the other source."""
    return {
        "tier": est["tier_used"],
        "comparable_count": est["comparable_count"],
        "median_asking_eur": est["market_median"],
        "market_p25_eur": est["market_p25"],
        "market_p75_eur": est["market_p75"],
        "price_difference_eur": est["price_difference"],   # median - asking
        "undervaluation_pct": est["undervaluation_pct"],   # (median-asking)/asking*100
        "insufficient": est["insufficient_sample"],
        "unknown_year_km_frac": est["unknown_year_km_frac"],
        "outliers_trimmed": est["outliers_trimmed"],
        "sample_warning": est.get("sample_warning") or "",
        "retrieval_error": err or None,
        # Mileage similarity of THIS source's comparables (never merged with the
        # other source). `mileage_match` is the category; `mileage` is the full
        # transparent breakdown (median/P25/P75, gap, direction, note).
        "mileage_match": est["mileage_match"],
        "mileage": est["mileage"],
        "comparables": _comparables(matched),
    }


def _agreement(ab_med, bz_med) -> tuple[Optional[float], Optional[str]]:
    if ab_med and bz_med:
        hi, lo = max(ab_med, bz_med), min(ab_med, bz_med)
        spread = (hi - lo) / hi * 100.0
        band = ("agree" if spread <= AGREE_MAX
                else "meaningful" if spread <= DISAGREE_MAX
                else "large")
        return round(spread, 1), band
    return None, None


def _build_result(car: InvCar, row: dict, ab_est: dict, bz_est: dict,
                  ab_matched: pd.DataFrame, bz_matched: pd.DataFrame,
                  ab_err: str, bz_err: str) -> dict:
    ab = _source_result(ab_est, ab_matched, ab_err)
    bz = _source_result(bz_est, bz_matched, bz_err)

    spread, band = _agreement(ab["median_asking_eur"], bz["median_asking_eur"])

    # Explicit, transparent warnings (never silently swallowed).
    warnings: list[str] = []
    if ab["sample_warning"]:
        warnings.append(f"autobazar: {ab['sample_warning']}")
    if bz["sample_warning"]:
        warnings.append(f"bazos: {bz['sample_warning']}")
    if ab["retrieval_error"]:
        warnings.append(f"autobazar retrieval: {ab['retrieval_error']}")
    if bz["retrieval_error"]:
        warnings.append(f"bazos retrieval: {bz['retrieval_error']}")
    ab_ok = not ab["insufficient"]
    bz_ok = not bz["insufficient"]
    if ab_ok and bz_ok and band == "large":
        warnings.append(f"large source disagreement: medians differ by {spread:.0f}%")
    if ab_ok != bz_ok:
        only = "autobazar" if ab_ok else "bazos"
        warnings.append(f"only {only} has a usable sample; no cross-source corroboration")

    insufficient = not (ab_ok or bz_ok)

    return {
        "car": {
            "brand": car.brand,
            "model": car.model,
            "variant": car.variant_raw,
            "variant_engine": car.variant_engine,
            "year": car.year,
            "fuel": car.fuel,
            "km": car.km,
            "asking_price_eur": car.price,
            "power_kw": car.power_kw,
            "power_source": car.power_source,
            "transmission": car.transmission,
            "body_type": car.body_type,
        },
        # Independent per-source results - deliberately NOT merged.
        "sources": {"autobazar": ab, "bazos": bz},
        "cross_source": {
            "median_spread_pct": spread,
            "agreement": band,                       # agree | meaningful | large | None
            "ab_vs_bz_pct_gap": row["ab_vs_bz_pct_gap"],
        },
        "confidence": {
            "flag": row["confidence_flag"],          # HIGH | MEDIUM | LOW | INSUFFICIENT
            "reasons": row["confidence_reasons"],
            "warnings": warnings,
        },
        "insufficient": insufficient,
        # For reference only: which single source the pipeline would rank on
        # (the one with more comparables). This is NOT a blended market price.
        "primary_source_for_ranking": row["rank_source"],
        "primary_undervaluation_pct": row["rank_price_diff_pct"],
        "missing_critical_fields": row["missing_critical_fields"],
    }


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def compare_single_car(
    inp: SingleCarInput,
    max_pages: int = 3,
    delay: float = 1.0,
    retrieved: Optional[tuple] = None,
) -> dict:
    """
    Retrieve comparables for one car from both sources and run the shared
    comparison engine, returning a structured, source-separated result.

    `retrieved`, when supplied, is a pre-fetched
    (ab_df, ab_err, bz_df, bz_err) tuple; passing it skips live HTTP (used by
    tests to compare against inventory mode on identical market data).
    """
    car = build_canonical_car(inp)

    if retrieved is None:
        ab_df, ab_err = retrieve_autobazar(car, max_pages, delay)
        bz_df, bz_err = retrieve_bazos(car, max_pages, delay)
        ab_err, bz_err = ab_err or "", bz_err or ""
    else:
        ab_df, ab_err, bz_df, bz_err = retrieved

    row, ab_est, bz_est, ab_matched, bz_matched = evaluate_car(
        car, ab_df, bz_df, ab_err, bz_err
    )
    return _build_result(
        car, row, ab_est, bz_est, ab_matched, bz_matched, ab_err, bz_err
    )


if __name__ == "__main__":  # pragma: no cover - manual smoke usage
    import json
    import sys

    demo = SingleCarInput(
        brand="Toyota", model="RAV4", variant="2.5 HSD 218HP Executive",
        year=2021, fuel="Hybrid", km=60000, price=32000,
    )
    res = compare_single_car(demo, max_pages=int(sys.argv[1]) if len(sys.argv) > 1 else 3)
    # Trim comparables for console readability.
    for s in res["sources"].values():
        s["comparables"] = s["comparables"][:5]
    print(json.dumps(res, indent=2, ensure_ascii=False))
