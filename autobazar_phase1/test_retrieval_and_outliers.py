"""
Regression tests for the Phase 6/7 retrieval + robustness fixes.

Covers, deterministically and OFFLINE (no network):

  1. Autobazar model-slug candidate generation (BMW 'N Series' -> 'rad n',
     Mercedes '-Class' -> 'trieda', VW 'ID.3' -> compact 'id3').
  2. Slug resolution with the live facet disabled (use_live_facet=False) so the
     constructed/hardcoded path is exercised without hitting the site.
  3. Bazos query normalization (strips '-Class'/' Series' suffixes that kill
     free-text recall).
  4. Single-listing (n=1) and implausible market/asking ratio outlier
     protection in estimate() -- the VW Touran -229.7% class of bug.
  5. The parts-title vehicle gate and the >=4 median-anchored outlier trim.

Run:  python test_retrieval_and_outliers.py
  or: python -m pytest test_retrieval_and_outliers.py -q
"""

from __future__ import annotations

import os
import sys

# Repointed to import the ACTUAL production modules in backend/engine/ rather
# than this directory's own (older) copies. Safe: phase5_compare.py is
# byte-identical between the two locations, and every function this file
# tests from phase6_validate.py (_model_slug_candidates, _ab_slugs,
# _bazos_query, estimate, qualifies_as_vehicle) is textually identical too --
# only the retrieval/timeout plumbing and the (here-untested) mileage_similarity
# addition have diverged. See the coverage-audit report for the verification.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "engine"))

import pandas as pd

from phase6_validate import (
    InvCar,
    _model_slug_candidates,
    _ab_slugs,
    _bazos_query,
    estimate,
    qualifies_as_vehicle,
)
from phase5_compare import price_stats


def _car(brand, model, price=15000, year=2020):
    return InvCar(
        row_index=0, brand=brand, model=model, variant_engine=None, fuel=None,
        year=year, km=None, price=price, power_kw=None, transmission=None,
        body_type=None, variant_raw=None, power_source="missing",
    )


# --------------------------------------------------------------------------- #
# 1. Model-slug candidates
# --------------------------------------------------------------------------- #
def test_slug_candidates_series():
    c = _model_slug_candidates("3 Series")
    assert "rad 3" in c and "rad-3" in c


def test_slug_candidates_class():
    c = _model_slug_candidates("A-Class")
    # Slovak 'trieda' form and the stripped stem must both be offered.
    assert "a trieda" in c and "a" in c


def test_slug_candidates_klasse():
    c = _model_slug_candidates("GLA-Klasse")
    assert "gla trieda" in c and "gla" in c


def test_slug_candidates_compact_dotted():
    c = _model_slug_candidates("ID.3")
    assert "id3" in c  # de-dotted compact form


def test_slug_candidates_plain():
    c = _model_slug_candidates("Octavia")
    assert c[0] == "octavia"


# --------------------------------------------------------------------------- #
# 2. Slug resolution (offline: facet disabled)
# --------------------------------------------------------------------------- #
def test_ab_slugs_hardcoded_quirk_offline():
    # VW ID.3 is in the hardcoded quick-path map -> resolves without network.
    b, m = _ab_slugs(_car("VW", "ID.3"), use_live_facet=False)
    assert (b, m) == ("volkswagen", "id3")


def test_ab_slugs_brand_alias_offline():
    b, _ = _ab_slugs(_car("VW", "Golf"), use_live_facet=False)
    assert b == "volkswagen"
    b2, _ = _ab_slugs(_car("Mercedes", "GLA"), use_live_facet=False)
    assert b2 == "mercedes-benz"


def test_ab_slugs_generic_fallback_offline():
    # No facet, no hardcode -> generic slugify of the model.
    b, m = _ab_slugs(_car("Skoda", "Octavia"), use_live_facet=False)
    assert m == "octavia"


# --------------------------------------------------------------------------- #
# 3. Bazos query normalization
# --------------------------------------------------------------------------- #
def test_bazos_query_strips_class():
    assert _bazos_query(_car("Mercedes", "GLA-Class")) == "Mercedes GLA"


def test_bazos_query_strips_series():
    assert _bazos_query(_car("BMW", "3 Series")) == "BMW 3"


def test_bazos_query_strips_accents_and_keeps_stem():
    # Accented model retained as stem (Bazos handles the encoded char fine).
    assert _bazos_query(_car("Renault", "Mégane")) == "Renault Megane"


def test_bazos_query_plain_unchanged():
    assert _bazos_query(_car("Nissan", "Qashqai")) == "Nissan Qashqai"


# --------------------------------------------------------------------------- #
# 4. Outlier / single-listing protection in estimate()
# --------------------------------------------------------------------------- #
def _matched(prices, year=2020, km=50000):
    return pd.DataFrame(
        [{"price": p, "year": year, "km": km, "title": "car", "url": f"u{i}"}
         for i, p in enumerate(prices)]
    )


def test_estimate_single_listing_flagged():
    car = _car("VW", "Golf", price=15000)
    est = estimate(car, _matched([14000]))
    assert est["comparable_count"] == 1
    assert "single-listing" in est["sample_warning"]
    assert est["confidence"] == "low"  # never medium/high on n=1


def test_undervaluation_pct_is_relative_to_asking_price():
    # The real VW Touran case: asking EUR 12,200 vs a lone EUR 3,700 comparable.
    # Denominator must be the ASKING price, giving a sane -69.7% ("priced ~70%
    # above this sample"), NOT the -229.7% the market-median denominator produced.
    car = _car("VW", "Touran", price=12200)
    est = estimate(car, _matched([3700]))
    assert est["undervaluation_pct"] == round((3700 - 12200) / 12200 * 100, 1)
    assert est["undervaluation_pct"] == -69.7
    # And a genuine below-market deal reads positive:
    car2 = _car("VW", "ID.3", price=9000)
    est2 = estimate(car2, _matched([16000, 17000, 18000, 17500]))
    assert est2["undervaluation_pct"] > 0  # cheaper than market => positive


def test_estimate_implausible_low_ratio_suppressed():
    # A mispriced ad like a EUR 600 (deposit/monthly figure) vs a EUR 12,000
    # asking car: ratio 0.05 << 0.20 -> estimate suppressed as an artifact.
    car = _car("VW", "Touran", price=12000)
    est = estimate(car, _matched([600]))  # ratio 0.05, above the EUR 300 floor
    assert est["estimated_market_price"] is None
    assert est["undervaluation_pct"] is None
    assert est["confidence"] == "none"
    assert est["insufficient_sample"] is True
    assert "implausible market/asking ratio" in est["sample_warning"]


def test_estimate_implausible_high_ratio_suppressed():
    car = _car("Dacia", "Duster", price=3000)
    est = estimate(car, _matched([25000]))  # >5x asking
    assert est["estimated_market_price"] is None
    assert "implausible" in est["sample_warning"]


def test_estimate_plausible_kept():
    car = _car("VW", "Golf", price=15000)
    est = estimate(car, _matched([14000, 15500, 16000, 14800, 15200]))
    assert est["estimated_market_price"] is not None
    assert est["sample_warning"] == ""
    assert est["undervaluation_pct"] is not None
    assert est["confidence"] in ("medium", "high")


# --------------------------------------------------------------------------- #
# 5. Vehicle gate + median-anchored outlier trim
# --------------------------------------------------------------------------- #
def test_parts_ad_rejected():
    assert qualifies_as_vehicle(
        {"price": 2500, "year": None, "km": 108000,
         "title": "Predam kompletny motor 2.0 TDI"}
    ) is False


def test_real_cheap_car_kept():
    assert qualifies_as_vehicle(
        {"price": 900, "year": 2006, "km": 250000, "title": "VW Passat 1.9 TDI"}
    ) is True


def test_price_floor_rejects_parts_priced():
    assert qualifies_as_vehicle(
        {"price": 1, "year": None, "km": None, "title": "naraznik"}
    ) is False


def test_outlier_trim_drops_fatfinger():
    # A EUR 59,000 tag among cheap old Passats must be trimmed at n>=4.
    stats = price_stats(_matched([1600, 2100, 2495, 2900, 3300, 59000]))
    assert stats["outliers_trimmed"] >= 1
    assert stats["max"] < 59000
    assert 2000 <= stats["median"] <= 3500


def test_outlier_trim_inactive_small_sample():
    # Below 4 prices the median is too unstable -> no trimming.
    stats = price_stats(_matched([2000, 40000]))
    assert stats["outliers_trimmed"] == 0


# --------------------------------------------------------------------------- #
# Tiny built-in runner (works without pytest installed).
# --------------------------------------------------------------------------- #
def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc!r}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {exc!r}")
    print(f"\n{passed} passed, {failed} failed, {len(fns)} total")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
