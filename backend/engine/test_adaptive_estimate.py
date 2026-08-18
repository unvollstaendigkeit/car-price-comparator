"""
Direct, offline tests for adaptive_estimate()'s tier-escalation logic against
the ACTUAL production backend/engine/phase6_validate.py (no separate/legacy
copy involved -- this file lives next to it and imports it directly).

Covers the STRICT -> MODERATE -> BROAD escalation documented at
adaptive_estimate()'s call site: the tightest tier reaching MIN_USABLE (4)
comparables is chosen; if none do, BROAD is used anyway and flagged
insufficient. No network access: all market data is synthetic.

Run: python test_adaptive_estimate.py
  or: python -m pytest test_adaptive_estimate.py -q
"""
from __future__ import annotations

import pandas as pd

from phase6_validate import InvCar, adaptive_estimate, MIN_USABLE

_MARKET_COLS = ["source", "listing_id", "title", "year", "km", "variant_engine",
                "power_kw", "transmission", "fuel", "body_type", "price", "url"]


def _car(**overrides) -> InvCar:
    base = dict(
        row_index=0, brand="Skoda", model="Octavia", variant_engine="2.0 TDI",
        fuel="Diesel", year=2020, km=80_000, price=15_000, power_kw=110,
        transmission="Manual", body_type="Combi", variant_raw="2.0 TDI 150HP",
        power_source="structured",
    )
    base.update(overrides)
    return InvCar(**base)


def _rows(n: int, price_start: int = 14_000, price_step: int = 250, **field_overrides) -> pd.DataFrame:
    """n listings cloned from a Skoda Octavia spec (see _car), overridable per field."""
    rows = []
    for i in range(n):
        row = {
            "source": "autobazar",
            "listing_id": f"L{i}",
            "title": "Skoda Octavia 2.0 TDI",
            "year": 2020,
            "km": 80_000,
            "variant_engine": "2.0 TDI",
            "power_kw": 110,
            "transmission": "Manual",
            "fuel": "Diesel",
            "body_type": "Combi",
            "price": price_start + i * price_step,
            "url": f"https://example.test/{i}",
        }
        row.update(field_overrides)
        rows.append(row)
    return pd.DataFrame(rows, columns=_MARKET_COLS)


# --------------------------------------------------------------------------- #
# STRICT reaches MIN_USABLE and is selected
# --------------------------------------------------------------------------- #
def test_strict_tier_selected_when_it_reaches_min_usable():
    car = _car()
    df = _rows(5)  # exact spec match on every field -> passes STRICT, MODERATE, BROAD alike
    est, matched = adaptive_estimate(car, df)
    assert est["tier_used"] == "strict"
    assert est["comparable_count"] == 5
    assert est["insufficient_sample"] is False
    assert est["tier_counts"] == "str:5/mod:5/bro:5"
    assert len(matched) == 5


# --------------------------------------------------------------------------- #
# STRICT insufficient (fails on transmission, which only STRICT requires) ->
# MODERATE selected
# --------------------------------------------------------------------------- #
def test_moderate_selected_when_strict_insufficient():
    car = _car()  # car.transmission == "Manual"
    df = _rows(4, transmission="Automatic")  # mismatched transmission -> STRICT rejects all
    est, matched = adaptive_estimate(car, df)
    assert est["tier_used"] == "moderate"
    assert est["comparable_count"] == 4
    assert est["insufficient_sample"] is False
    assert est["tier_counts"] == "str:0/mod:4/bro:4"
    assert len(matched) == 4


# --------------------------------------------------------------------------- #
# STRICT and MODERATE both insufficient (year 3 off: outside both's tolerance,
# 1 and 2, but within BROAD's tolerance of 3) -> BROAD selected
# --------------------------------------------------------------------------- #
def test_broad_selected_when_moderate_insufficient():
    car = _car()  # car.year == 2020
    df = _rows(4, year=2017)  # |2020-2017| = 3: fails STRICT(tol1)/MODERATE(tol2), passes BROAD(tol3)
    est, matched = adaptive_estimate(car, df)
    assert est["tier_used"] == "broad"
    assert est["comparable_count"] == 4
    assert est["insufficient_sample"] is False
    assert est["tier_counts"] == "str:0/mod:0/bro:4"
    assert len(matched) == 4


# --------------------------------------------------------------------------- #
# Even BROAD stays under MIN_USABLE -> falls back to BROAD, flagged insufficient
# --------------------------------------------------------------------------- #
def test_broad_still_insufficient_is_flagged():
    car = _car()
    df = _rows(2)  # only 2 listings total, well under MIN_USABLE=4, at every tier
    est, matched = adaptive_estimate(car, df)
    assert MIN_USABLE == 4  # pin the constant this test's arithmetic depends on
    assert est["tier_used"] == "broad"
    assert est["comparable_count"] == 2
    assert est["insufficient_sample"] is True
    assert est["tier_counts"] == "str:2/mod:2/bro:2"
    assert len(matched) == 2


def test_empty_pool_is_insufficient_broad():
    car = _car()
    df = pd.DataFrame(columns=_MARKET_COLS)
    est, matched = adaptive_estimate(car, df)
    assert est["tier_used"] == "broad"
    assert est["comparable_count"] == 0
    assert est["insufficient_sample"] is True
    assert matched.empty


# --------------------------------------------------------------------------- #
# Runner (pytest-free fallback, matching this project's existing test files)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    raise SystemExit(1 if failed else 0)
