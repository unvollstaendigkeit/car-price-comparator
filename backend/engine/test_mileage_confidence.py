"""
Tests for the mileage-similarity confidence factor.

Two layers are covered:
  1. mileage_similarity()  -- categorization of the comparable-mileage
     distribution vs the submitted car (phase6_validate.py).
  2. confidence_flag()     -- how a mileage category caps/downgrades the overall
     HIGH/MEDIUM/LOW/INSUFFICIENT flag (phase7_fullrun.py), WITHOUT touching any
     price figure.

The motivating real case: a high-mileage car (e.g. a ~280,000 km VW Polo) whose
comparable pool is dominated by ~100-120k km listings must NOT be reported as a
HIGH-confidence bargain, because the median it is compared against reflects
much lower-mileage cars.

Runs under pytest (`python -m pytest test_mileage_confidence.py`) OR standalone
(`python test_mileage_confidence.py`).
"""

from phase6_validate import (
    mileage_similarity,
    MILEAGE_NOISE_FLOOR_KM,
    MILEAGE_GOOD_MAX,
    MILEAGE_MODERATE_MAX,
)
from phase7_fullrun import confidence_flag, InvCar


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _est(comparable_count, median, mileage, unknown_frac=0.0, tier_used="strict"):
    """Minimal per-source est dict shaped like phase6_validate.estimate().
    2026-08-25: confidence_flag() is now tier-driven (tier_used sets the
    baseline: strict->HIGH, moderate->MEDIUM, broad->LOW), so this file's
    tests -- which are purely about the mileage-driven CAPPING behavior on
    top of that baseline, not about tier selection itself (see
    test_adaptive_estimate.py for that) -- default to "strict" so the
    baseline starts at HIGH and each test's mileage scenario is what pulls
    it down, same intent as before this became tier-aware."""
    return {
        "comparable_count": comparable_count,
        "market_median": median,
        "unknown_year_km_frac": unknown_frac,
        "mileage_match": mileage["category"],
        "mileage": mileage,
        "insufficient_sample": comparable_count == 0,
        "tier_used": tier_used,
    }


def _car(km=150000, year=2016, fuel="Diesel", body="Hatchback"):
    return InvCar(
        row_index=0, brand="VW", model="Golf",
        variant_engine="2.0 TDI", fuel=fuel, year=year, km=km, price=8000,
        power_kw=110, transmission="manual", body_type=body,
        variant_raw="2.0 TDI", power_source="ice",
    )


def _good():
    return mileage_similarity(150000, [145000, 150000, 155000, 148000])


# --------------------------------------------------------------------------- #
# 1. mileage_similarity categorization
# --------------------------------------------------------------------------- #
def test_good_when_close():
    m = mileage_similarity(150000, [145000, 150000, 155000, 148000])
    assert m["category"] == "good"
    assert m["direction"] in ("higher", "lower", "same")


def test_noise_floor_is_good():
    # Gap of ~10k km on a 60k car: below the 20k absolute floor -> always good.
    m = mileage_similarity(60000, [68000, 70000, 66000, 72000])
    assert m["abs_gap_km"] <= MILEAGE_NOISE_FLOOR_KM
    assert m["category"] == "good"


def test_moderate_gap():
    # ~45% higher: between GOOD (0.30) and MODERATE (0.60).
    m = mileage_similarity(100000, [140000, 150000, 145000, 148000])
    assert m["category"] == "moderate"
    assert m["direction"] == "higher"


def test_very_large_high_mileage_car():
    # The motivating case: 280k km car vs ~110k km comparables.
    m = mileage_similarity(280000, [100000, 110000, 120000, 115000])
    assert m["category"] == "very_large"
    assert m["direction"] == "lower"          # comparables have LESS mileage
    assert m["comp_km_median"] and m["comp_km_median"] < 150000


def test_large_gap_boundary():
    # median exactly ~2x the car -> rel ~1.0 -> large (not yet very_large).
    m = mileage_similarity(100000, [195000, 200000, 205000, 198000])
    assert m["category"] in ("large", "very_large")


def test_unknown_when_no_submitted_km():
    m = mileage_similarity(None, [100000, 120000])
    assert m["category"] == "unknown"
    assert "unknown" in m["note"].lower()


def test_unknown_when_no_comparable_km():
    m = mileage_similarity(120000, [None, None, None])
    assert m["category"] == "unknown"
    assert m["comp_km_count"] == 0


def test_direction_reported_for_lower_mileage_comps():
    m = mileage_similarity(250000, [90000, 100000, 110000])
    assert m["direction"] == "lower"
    assert m["rel_gap"] is not None and m["rel_gap"] < 0  # signed: comps lower


def test_close_frac_computed():
    m = mileage_similarity(150000, [150000, 152000, 300000, 148000])
    # 3 of 4 within the STRICT band around 150k.
    assert m["close_frac"] == 0.75


# --------------------------------------------------------------------------- #
# 2. confidence_flag mileage capping
# --------------------------------------------------------------------------- #
def test_high_requires_good_mileage_both_sources():
    good = _good()
    ab = _est(10, 8000, good)
    bz = _est(9, 8100, good)          # medians within AGREE_MAX
    flag, reasons = confidence_flag(_car(), ab, bz)
    assert flag == "HIGH"
    assert "mileage closely matches" in reasons


def test_very_large_mileage_blocks_high_forces_low():
    good = _good()
    bad = mileage_similarity(280000, [100000, 110000, 120000, 115000])
    ab = _est(12, 8000, bad)
    bz = _est(11, 8050, good)         # even if the other source is fine
    flag, reasons = confidence_flag(_car(km=280000), ab, bz)
    assert flag == "LOW"
    assert "mileage" in reasons.lower()


def test_moderate_mileage_caps_at_medium():
    good = _good()
    mod = mileage_similarity(100000, [140000, 150000, 145000, 148000])  # moderate
    ab = _est(10, 8000, mod)
    bz = _est(9, 8100, good)
    flag, reasons = confidence_flag(_car(km=100000), ab, bz)
    assert flag == "MEDIUM"
    assert "mileage" in reasons.lower()


def test_unverifiable_comparable_mileage_caps_at_medium():
    # Submitted km IS known (so inventory is complete), but the comparables have
    # no km at all -> mileage is 'unknown' -> capped at MEDIUM, never HIGH, and
    # never forced to INSUFFICIENT by mileage alone.
    unk = mileage_similarity(150000, [None, None, None, None])
    assert unk["category"] == "unknown"
    ab = _est(10, 8000, unk)
    bz = _est(9, 8100, unk)
    flag, reasons = confidence_flag(_car(), ab, bz)
    assert flag == "MEDIUM"
    assert "mileage" in reasons.lower()


def test_insufficient_only_when_both_sources_have_zero_comparables():
    # 2026-08-25: a nonzero count (even 1) is no longer "insufficient" on its
    # own -- see test_adaptive_estimate.py / test_evaluate_car.py. Only a
    # genuine zero-comparables-anywhere case reaches INSUFFICIENT here,
    # regardless of mileage.
    good = _good()
    ab = _est(0, None, good)
    bz = _est(0, None, good)
    flag, _ = confidence_flag(_car(), ab, bz)
    assert flag == "INSUFFICIENT"


def test_mileage_does_not_change_price_median():
    # Sanity: the est dict's median is untouched regardless of mileage category.
    bad = mileage_similarity(280000, [100000, 110000, 120000])
    ab = _est(10, 8000, bad)
    assert ab["market_median"] == 8000


# --------------------------------------------------------------------------- #
# Standalone runner
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    fns = [g for n, g in sorted(globals().items()) if n.startswith("test_") and callable(g)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
