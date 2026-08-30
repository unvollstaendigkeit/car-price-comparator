"""
Direct, offline tests for adaptive_estimate()'s tier-escalation logic against
the ACTUAL production backend/engine/phase6_validate.py (no separate/legacy
copy involved -- this file lives next to it and imports it directly).

Covers the STRICT -> MODERATE -> BROAD escalation documented at
adaptive_estimate()'s call site: the tightest tier reaching ITS OWN
usability floor is chosen -- 1 for STRICT, MIN_USABLE (4) for MODERATE/
BROAD (2026-08-25: STRICT's floor was previously also MIN_USABLE, so a
single precise STRICT match was passed over for a larger but less precise
MODERATE/BROAD pool, or reported as "insufficient" outright). If no tier
reaches its floor, BROAD is used anyway and flagged insufficient. No
network access: all market data is synthetic.

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
# STRICT insufficient (fails on power, +/-15kW: outside STRICT's +/-10kW but
# within MODERATE's +/-20kW) -> MODERATE selected.
# (2026-08-28 business sign-off tightened MODERATE to also require the SAME
# transmission/variant as STRICT -- only BROAD drops those -- so a
# transmission-only mismatch no longer reaches MODERATE; power is the
# differentiator that still isolates "STRICT-only" the way this test needs.)
# --------------------------------------------------------------------------- #
def test_moderate_selected_when_strict_insufficient():
    car = _car()  # car.power_kw == 110
    df = _rows(4, power_kw=125)  # +15kW -> fails STRICT(+/-10), passes MODERATE(+/-20)
    est, matched = adaptive_estimate(car, df)
    assert est["tier_used"] == "moderate"
    assert est["comparable_count"] == 4
    assert est["insufficient_sample"] is False
    assert est["tier_counts"] == "str:0/mod:4/bro:4"
    assert len(matched) == 4


# --------------------------------------------------------------------------- #
# STRICT and MODERATE both insufficient (year 3 off: outside both's tolerance,
# 1 and 2, but within BROAD's tolerance of 3) -> BROAD selected.
# Uses 2023, not 2017: the car is a Skoda Octavia (year 2020, Mk4 in the
# generation-awareness table added 2026-08-28), and 2017 falls in Mk3 --
# rule_generation would hard-block that pairing regardless of tier, which is
# correct real-world behavior but not what THIS test is exercising (pure
# year-tolerance escalation). 2023 stays within the same Mk4 range.
# --------------------------------------------------------------------------- #
def test_broad_selected_when_moderate_insufficient():
    car = _car()  # car.year == 2020
    df = _rows(4, year=2023)  # |2020-2023| = 3: fails STRICT(tol1)/MODERATE(tol2), passes BROAD(tol3)
    est, matched = adaptive_estimate(car, df)
    assert est["tier_used"] == "broad"
    assert est["comparable_count"] == 4
    assert est["insufficient_sample"] is False
    assert est["tier_counts"] == "str:0/mod:0/bro:4"
    assert len(matched) == 4


# --------------------------------------------------------------------------- #
# 2026-08-25: MIN_USABLE no longer gates SELECTION at any tier -- a match at
# BROAD alone is now reported as usable (comparable_count > 0), not flagged
# insufficient just for being under the old count floor. Only a truly empty
# pool (see test_empty_pool_is_insufficient_broad) is insufficient. This
# replaces the old "BROAD stays under MIN_USABLE -> insufficient" case,
# which no longer exists as a concept.
# --------------------------------------------------------------------------- #
def test_broad_only_match_is_now_sufficient():
    car = _car()  # car.year == 2020, car.transmission == "Manual"
    # transmission mismatch rejects STRICT AND MODERATE (2026-08-28: MODERATE
    # now also requires transmission/variant); year off by 3 -- kept within
    # the same generation bucket (2023, not 2017 -- see
    # test_broad_selected_when_moderate_insufficient's comment) -- rejects
    # MODERATE(tol 2) too, leaving only BROAD (tol 3) -- just 2 listings,
    # well under the OLD MIN_USABLE=4, but that no longer matters for
    # selection or for insufficient_sample.
    df = _rows(2, year=2023, transmission="Automatic")
    est, matched = adaptive_estimate(car, df)
    assert MIN_USABLE == 4  # pin the constant confidence_flag() now uses instead
    assert est["tier_used"] == "broad"
    assert est["comparable_count"] == 2
    assert est["insufficient_sample"] is False
    assert est["tier_counts"] == "str:0/mod:0/bro:2"
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
# 2026-08-25: a lone STRICT comparable is trusted on its own -- unlike
# MODERATE/BROAD, it doesn't need MIN_USABLE to be reported as usable.
# --------------------------------------------------------------------------- #
def test_single_strict_comparable_is_sufficient():
    car = _car()
    df = _rows(1)  # exact spec match on every field -> the 1 listing passes STRICT
    est, matched = adaptive_estimate(car, df)
    assert est["tier_used"] == "strict"
    assert est["comparable_count"] == 1
    assert est["insufficient_sample"] is False
    assert est["tier_counts"] == "str:1/mod:1/bro:1"
    assert len(matched) == 1


def test_few_strict_comparables_below_min_usable_still_sufficient():
    car = _car()
    df = _rows(3)  # 3 exact matches: below MIN_USABLE=4, still trusted at STRICT
    est, matched = adaptive_estimate(car, df)
    assert est["tier_used"] == "strict"
    assert est["comparable_count"] == 3
    assert est["insufficient_sample"] is False


def test_single_moderate_comparable_is_selected_and_sufficient():
    # power +15kW rejects STRICT (+/-10kW) only; the 1 listing reaches
    # MODERATE (+/-20kW) and is now selected there directly (2026-08-25:
    # MODERATE no longer needs MIN_USABLE=4 to be chosen over falling
    # through to BROAD). See test_moderate_selected_when_strict_insufficient
    # for why power, not transmission, is the differentiator here.
    car = _car()
    df = _rows(1, power_kw=125)
    est, matched = adaptive_estimate(car, df)
    assert est["tier_used"] == "moderate"
    assert est["tier_counts"] == "str:0/mod:1/bro:1"
    assert est["comparable_count"] == 1
    assert est["insufficient_sample"] is False


def test_single_broad_comparable_is_selected_and_sufficient():
    car = _car()  # car.year == 2020
    df = _rows(1, year=2023, transmission="Automatic")  # only reaches BROAD
    est, matched = adaptive_estimate(car, df)
    assert est["tier_used"] == "broad"
    assert est["comparable_count"] == 1
    assert est["insufficient_sample"] is False


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
