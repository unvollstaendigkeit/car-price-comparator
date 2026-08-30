"""
Tests for the require_known_variant/require_known_transmission STRICT/
MODERATE gate (2026-08-30) -- had ZERO test coverage before this file
despite being the exact bug behind a user-reported live-recall audit:
listings with an UNKNOWN variant_engine or transmission were passing
STRICT/MODERATE's "must match" rules via the project's general "unknown
never fails" missing-data policy, in violation of the explicit business
rule "a different transmission can be a broad match, never moderate or
strict" -- an unknown value that's secretly different is the same failure
by another name. Confirmed against 3 real cases: a Bazoš listing with only
a bare title ("Škoda octavia"), a Bazoš listing that was ALSO all-unknown
("Predám škoda Octavia 3 rok 2016"), and an Autobazar listing
(AbqUHYVXV4D) whose transmission is unextracted there today (Autobazar
never gets a variant_engine value at all -- see
bridge/to_display_fields.py's _load_autobazar_incremental) but is
genuinely manual per a live check, yet was landing in strict/moderate.

Mirrors rule_year/require_known_year, the pre-existing precedent for this
exact "must be known, not just match-if-known" pattern.

Run: python test_strict_tier_known_field_requirement.py
  or: python -m pytest test_strict_tier_known_field_requirement.py -q
"""
from __future__ import annotations

import pandas as pd

from phase5_compare import STRICT, MODERATE, BROAD
from phase6_validate import (
    InvCar, evaluate, rule_variant, rule_transmission, rule_fuel, rule_km,
    MATCH, MISMATCH, UNKNOWN, NA,
)


def _car(**overrides) -> InvCar:
    base = dict(
        row_index=0, brand="Skoda", model="Octavia", variant_engine="2.0 TDI",
        fuel="Diesel", year=2016, km=241_000, price=4_900, power_kw=110,
        transmission="Automatic", body_type="Combi", variant_raw="2,0 TDI Style DSG 150HK",
        power_source="structured",
    )
    base.update(overrides)
    return InvCar(**base)


def _row(**overrides) -> pd.Series:
    base = dict(
        source="bazos", listing_id="x", title="Skoda Octavia", year=2016, km=241_000,
        variant_engine="2.0 TDI", power_kw=110, transmission="Automatic", fuel="Diesel",
        body_type="Combi", price=4_900, url="https://example.test/x",
    )
    base.update(overrides)
    return pd.Series(base)


# --------------------------------------------------------------------------- #
# 1. rule_variant / rule_transmission -- direct unit-level require_known checks.
# --------------------------------------------------------------------------- #
def test_rule_variant_unknown_passes_when_require_known_is_false():
    assert rule_variant(_car(), _row(variant_engine=None), require=True, require_known=False) == UNKNOWN


def test_rule_variant_unknown_fails_when_require_known_is_true():
    assert rule_variant(_car(), _row(variant_engine=None), require=True, require_known=True) == MISMATCH


def test_rule_variant_known_match_still_passes_with_require_known_true():
    assert rule_variant(_car(), _row(variant_engine="2.0 TDI"), require=True, require_known=True) == MATCH


def test_rule_variant_known_mismatch_still_fails_with_require_known_true():
    assert rule_variant(_car(), _row(variant_engine="1.4 TSI"), require=True, require_known=True) == MISMATCH


def test_rule_variant_not_required_stays_na_regardless_of_require_known():
    """BROAD sets require_same_variant=False -- require_known must never
    override that (an unknown variant on a require=False tier stays NA,
    never becomes a MISMATCH)."""
    assert rule_variant(_car(), _row(variant_engine=None), require=False, require_known=True) == NA


def test_rule_transmission_unknown_passes_when_require_known_is_false():
    assert rule_transmission(_car(), _row(transmission=None), require=True, require_known=False) == UNKNOWN


def test_rule_transmission_unknown_fails_when_require_known_is_true():
    assert rule_transmission(_car(), _row(transmission=None), require=True, require_known=True) == MISMATCH


def test_rule_transmission_known_match_still_passes_with_require_known_true():
    assert rule_transmission(_car(), _row(transmission="Automatic"), require=True, require_known=True) == MATCH


def test_rule_transmission_known_mismatch_still_fails_with_require_known_true():
    assert rule_transmission(_car(), _row(transmission="Manual"), require=True, require_known=True) == MISMATCH


def test_rule_transmission_not_required_stays_na_regardless_of_require_known():
    assert rule_transmission(_car(), _row(transmission=None), require=False, require_known=True) == NA


def test_rule_fuel_unknown_passes_when_require_known_is_false():
    assert rule_fuel(_car(), _row(fuel=None), require_known=False) == UNKNOWN


def test_rule_fuel_unknown_fails_when_require_known_is_true():
    assert rule_fuel(_car(), _row(fuel=None), require_known=True) == MISMATCH


def test_rule_fuel_known_match_still_passes_with_require_known_true():
    assert rule_fuel(_car(), _row(fuel="Diesel"), require_known=True) == MATCH


def test_rule_fuel_known_mismatch_still_fails_with_require_known_true():
    assert rule_fuel(_car(), _row(fuel="Petrol"), require_known=True) == MISMATCH


def test_rule_fuel_na_when_car_fuel_itself_unknown_regardless_of_require_known():
    assert rule_fuel(_car(fuel=None), _row(fuel=None), require_known=True) == NA


def test_rule_km_unknown_passes_when_require_known_is_false():
    assert rule_km(_car(), _row(km=None), pct=0.0, floor=20_000, require_known=False) == UNKNOWN


def test_rule_km_unknown_fails_when_require_known_is_true():
    assert rule_km(_car(), _row(km=None), pct=0.0, floor=20_000, require_known=True) == MISMATCH


def test_rule_km_known_match_still_passes_with_require_known_true():
    assert rule_km(_car(), _row(km=245_000), pct=0.0, floor=20_000, require_known=True) == MATCH


def test_rule_km_known_mismatch_still_fails_with_require_known_true():
    assert rule_km(_car(), _row(km=500_000), pct=0.0, floor=20_000, require_known=True) == MISMATCH


def test_rule_km_na_when_car_km_itself_unknown_regardless_of_require_known():
    assert rule_km(_car(km=None), _row(km=None), pct=0.0, floor=20_000, require_known=True) == NA


# --------------------------------------------------------------------------- #
# 2. evaluate() with the real STRICT/MODERATE/BROAD tiers -- confirms the
# RuleSet fields are actually wired through, not just unit-tested rules.
# --------------------------------------------------------------------------- #
def test_strict_rejects_unknown_variant():
    car = _car()
    row = _row(variant_engine=None)
    passed, outcomes = evaluate(car, row, STRICT)
    assert passed is False
    assert outcomes["variant"] == MISMATCH


def test_strict_rejects_unknown_transmission():
    car = _car()
    row = _row(transmission=None)
    passed, outcomes = evaluate(car, row, STRICT)
    assert passed is False
    assert outcomes["transmission"] == MISMATCH


def test_moderate_rejects_unknown_variant():
    car = _car()
    row = _row(variant_engine=None)
    passed, outcomes = evaluate(car, row, MODERATE)
    assert passed is False
    assert outcomes["variant"] == MISMATCH


def test_moderate_rejects_unknown_transmission():
    car = _car()
    row = _row(transmission=None)
    passed, outcomes = evaluate(car, row, MODERATE)
    assert passed is False
    assert outcomes["transmission"] == MISMATCH


def test_broad_still_accepts_unknown_variant_and_transmission():
    """BROAD deliberately drops variant/transmission entirely
    (require_same_variant=require_same_transmission=False) -- the new
    require_known_* fields must never change BROAD's behavior."""
    car = _car()
    row = _row(variant_engine=None, transmission=None)
    passed, outcomes = evaluate(car, row, BROAD)
    assert passed is True
    assert outcomes["variant"] == NA
    assert outcomes["transmission"] == NA


def test_strict_still_accepts_a_genuine_known_match_regression_guard():
    """Regression guard: a listing with a real, verified matching variant
    AND transmission (the normal/common case) must still pass STRICT --
    this change must only reject the UNKNOWN case, nothing else."""
    car = _car()
    row = _row()  # variant_engine="2.0 TDI", transmission="Automatic" -- matches car
    passed, outcomes = evaluate(car, row, STRICT)
    assert passed is True
    assert outcomes["variant"] == MATCH
    assert outcomes["transmission"] == MATCH


def test_strict_still_rejects_a_genuine_known_mismatch_regression_guard():
    car = _car()
    row = _row(transmission="Manual")  # car is Automatic (DSG)
    passed, outcomes = evaluate(car, row, STRICT)
    assert passed is False
    assert outcomes["transmission"] == MISMATCH


def test_strict_rejects_unknown_fuel():
    car = _car()
    row = _row(fuel=None)
    passed, outcomes = evaluate(car, row, STRICT)
    assert passed is False
    assert outcomes["fuel"] == MISMATCH


def test_moderate_rejects_unknown_fuel():
    car = _car()
    row = _row(fuel=None)
    passed, outcomes = evaluate(car, row, MODERATE)
    assert passed is False
    assert outcomes["fuel"] == MISMATCH


def test_strict_rejects_unknown_km():
    car = _car()
    row = _row(km=None)
    passed, outcomes = evaluate(car, row, STRICT)
    assert passed is False
    assert outcomes["km"] == MISMATCH


def test_moderate_rejects_unknown_km():
    car = _car()
    row = _row(km=None)
    passed, outcomes = evaluate(car, row, MODERATE)
    assert passed is False
    assert outcomes["km"] == MISMATCH


def test_broad_still_accepts_unknown_fuel_and_km():
    """BROAD must be untouched by the new require_known_fuel/require_known_km
    flags -- both default False there, same as require_known_variant/
    require_known_transmission."""
    car = _car()
    row = _row(fuel=None, km=None)
    passed, outcomes = evaluate(car, row, BROAD)
    assert passed is True
    assert outcomes["fuel"] == UNKNOWN
    assert outcomes["km"] == UNKNOWN


def test_strict_still_accepts_a_genuine_known_fuel_and_km_match_regression_guard():
    car = _car()
    row = _row()  # fuel="Diesel", km=241_000 -- both match car
    passed, outcomes = evaluate(car, row, STRICT)
    assert passed is True
    assert outcomes["fuel"] == MATCH
    assert outcomes["km"] == MATCH


# --------------------------------------------------------------------------- #
# 3. The exact reported live cases.
# --------------------------------------------------------------------------- #
def test_bare_title_listing_no_longer_strict_matches():
    """Listing 194372399: title 'Škoda octavia', nothing else extracted --
    reported by the user as a false STRICT match."""
    car = _car()
    row = _row(variant_engine=None, transmission=None, fuel=None, km=None)
    passed, _outcomes = evaluate(car, row, STRICT)
    assert passed is False


def test_autobazar_listing_with_no_extracted_transmission_no_longer_strict_or_moderate_matches():
    """AbqUHYVXV4D: a real manual-transmission Autobazar Octavia with
    transmission=None in the DB (Autobazar never gets a variant_engine
    value at all today) -- must never land in strict OR moderate per the
    explicit business rule ("different transmission -> broad only")."""
    car = _car()
    row = _row(variant_engine=None, transmission=None, fuel="Diesel", km=260_000, price=8_979)
    strict_passed, _ = evaluate(car, row, STRICT)
    moderate_passed, _ = evaluate(car, row, MODERATE)
    broad_passed, _ = evaluate(car, row, BROAD)
    assert strict_passed is False
    assert moderate_passed is False
    assert broad_passed is True  # still allowed at broad, per the business rule


# --------------------------------------------------------------------------- #
# Standalone runner (works without pytest)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"PASS  {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    raise SystemExit(1 if failed else 0)
