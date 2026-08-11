"""
Regression tests for the inventory input-normalization layer.

Plain asserts + a built-in runner (no pytest dependency), matching the project
convention. Covers BOTH real column layouts (format A and format B), the
Volvo PHEV/EV examples, the SOLD price, header disambiguation, ambiguity
reporting, manual overrides, and single-car normalization.

Run: python test_inventory_normalizer.py
"""

from __future__ import annotations

import pandas as pd

from inventory_normalizer import (
    normalize_inventory,
    normalize_record,
    norm_fuel,
    norm_year,
    norm_price,
)
from inventory_loader import load_inventory

FIX_A = "tests/fixtures/inventory_format_a.csv"
FIX_B = "tests/fixtures/inventory_format_b.csv"


# --------------------------------------------------------------------------- #
# Header mapping across differing layouts
# --------------------------------------------------------------------------- #
def test_format_a_maps_all_canonical_fields():
    r = normalize_inventory(FIX_A)
    for f in ["year", "fuel", "brand", "model", "variant", "body_type",
              "vin", "colour", "km", "price", "pictures", "co2"]:
        assert f in r.mapping, f"format A failed to map {f}"
    assert not r.missing_required
    assert not r.ambiguities


def test_format_b_price_full_vat_maps_to_price():
    r = normalize_inventory(FIX_B)
    assert r.mapping["price"] == "Price full vat"
    assert not r.missing_required


def test_type_case_collision_disambiguated_by_values():
    # 'Type' (fuel values) vs 'type' (body values) differ only by case.
    for fix in (FIX_A, FIX_B):
        r = normalize_inventory(fix)
        assert r.mapping["fuel"] == "Type"
        assert r.mapping["body_type"] == "type"
        assert not r.ambiguities


def test_vin_and_co2_case_and_punctuation_synonyms():
    r = normalize_inventory(FIX_A)
    assert r.mapping["vin"] == "VIn"
    assert r.mapping["co2"] == "Co2"


# --------------------------------------------------------------------------- #
# Value normalization
# --------------------------------------------------------------------------- #
def test_fuel_canonicalization_ev_phev_variants():
    assert norm_fuel("El") == "Electric"
    assert norm_fuel("Electric") == "Electric"
    assert norm_fuel("Plug-in") == "PHEV"
    assert norm_fuel("Plug-in Hybrid") == "PHEV"
    assert norm_fuel("Benzin") == "Petrol"
    assert norm_fuel("Diesel") == "Diesel"
    assert norm_fuel("Hybrid") == "Hybrid"
    assert norm_fuel("nonsense") is None


def test_volvo_phev_and_ev_rows_normalize():
    a = normalize_inventory(FIX_A).rows.set_index("model")
    assert a.loc["XC40", "fuel"] == "Electric"      # Volvo EV
    assert a.loc["XC60", "fuel"] == "PHEV"           # Volvo PHEV (Plug-in)
    b = normalize_inventory(FIX_B).rows.set_index("model")
    assert b.loc["C40", "fuel"] == "Electric"        # Volvo EV (El)
    assert b.loc["V60", "fuel"] == "PHEV"            # Volvo PHEV (Plug-in Hybrid)


def test_year_from_bare_and_date():
    assert norm_year("2021") == (2021, "bare_year")
    y, src = norm_year("3/15/2022")
    assert y == 2022 and src.startswith("date")
    assert norm_year("")[0] is None


def test_km_thousands_separator_to_int():
    a = normalize_inventory(FIX_A).rows.set_index("model")
    assert a.loc["XC40", "km"] == 45000
    assert isinstance(a.loc["XC40", "km"], (int,)) or float(a.loc["XC40", "km"]).is_integer()


def test_co2_zero_preserved():
    a = normalize_inventory(FIX_A).rows.set_index("model")
    assert a.loc["XC40", "co2"] == 0            # EV, genuine zero (not missing)


# --------------------------------------------------------------------------- #
# SOLD / unavailable price handling
# --------------------------------------------------------------------------- #
def test_sold_price_is_valid_record_excluded_from_valuation():
    r = normalize_inventory(FIX_A)
    bmw = r.rows.set_index("model").loc["3 Series"]
    assert pd.isna(bmw["price"]) or bmw["price"] is None
    assert bmw["status"] == "sold"
    assert bmw["price_original"] == "SOLD"       # original preserved
    assert bmw["valid_for_comparison"] == False
    # A sold row is NOT counted as invalid.
    assert r.counts["invalid"] == 0
    assert r.counts["sold_or_unavailable"] == 1


def test_norm_price_status_taxonomy():
    assert norm_price("14,200") == (14200, "active", "14,200")
    assert norm_price("SOLD")[1] == "sold"
    assert norm_price("Reserved")[1] == "reserved"
    assert norm_price("")[1] == "missing"
    assert norm_price("n/a")[1] in ("unavailable", "sold")  # treated as unavailable


# --------------------------------------------------------------------------- #
# Ambiguity: do NOT guess
# --------------------------------------------------------------------------- #
def test_inconclusive_ambiguous_columns_are_reported_not_guessed():
    # Two bare 'type' columns whose values match neither fuel nor body vocab.
    df = pd.DataFrame({
        "Brand": ["VW"], "Model": ["Golf"], "Year": ["2020"],
        "Km": ["100000"], "Price": ["10000"],
        "Type": ["foo"], "type": ["bar"],
    })
    r = normalize_inventory(df)
    assert r.ambiguities, "expected an ambiguity to be reported, not a guess"
    # fuel/body should be unmapped because we refused to guess
    assert "fuel" not in r.mapping or "body_type" not in r.mapping


def test_manual_mapping_resolves_ambiguity():
    df = pd.DataFrame({
        "Brand": ["VW"], "Model": ["Golf"], "Year": ["2020"],
        "Km": ["100000"], "Price": ["10000"],
        "Type": ["foo"], "type": ["bar"],
    })
    r = normalize_inventory(df, manual_mapping={"Type": "fuel", "type": "body_type"})
    assert not r.ambiguities
    assert r.mapping["fuel"] == "Type"
    assert r.mapping["body_type"] == "type"


def test_missing_required_field_reported():
    df = pd.DataFrame({
        "Brand": ["VW"], "Model": ["Golf"], "Year": ["2020"],
        "Km": ["100000"], "Type": ["Benzin"],   # no price column
    })
    r = normalize_inventory(df)
    assert "price" in r.missing_required


def test_unmapped_columns_reported():
    df = pd.DataFrame({
        "Brand": ["VW"], "Model": ["Golf"], "Year": ["2020"],
        "Km": ["100000"], "Price": ["10000"], "Type": ["Benzin"],
        "Salesperson": ["Alice"],               # not a canonical field
    })
    r = normalize_inventory(df)
    assert "Salesperson" in r.unmapped_columns


# --------------------------------------------------------------------------- #
# Single-car (manual-entry) reuse
# --------------------------------------------------------------------------- #
def test_normalize_record_single_car():
    rec, issues = normalize_record({
        "Brand": "Volvo", "Model": "XC60", "Year": "2020",
        "Type": "Plug-in", "type": "SUV", "Km": "78,500", "Price": "29,500",
    })
    assert rec["brand"] == "Volvo"
    assert rec["fuel"] == "PHEV"
    assert rec["body_type"] == "SUV"
    assert rec["km"] == 78500
    assert rec["price"] == 29500
    assert rec["valid_for_comparison"] == True


# --------------------------------------------------------------------------- #
# Integration: same canonical layer feeds the comparison-engine loader
# --------------------------------------------------------------------------- #
def test_load_inventory_on_both_formats():
    for fix in (FIX_A, FIX_B):
        df = load_inventory(fix)
        assert len(df) == 5
        assert set(["brand", "model", "year", "fuel", "km", "price",
                    "power_kw", "variant_engine"]).issubset(df.columns)
    # SOLD provenance carried into availability
    a = load_inventory(FIX_A).set_index("model")
    assert "price_sold" in str(a.loc["3 Series", "availability"])


def test_load_inventory_real_sample_unchanged():
    df = load_inventory("inventory_sample.csv")
    assert len(df) == 98
    assert df["brand"].notna().all()
    assert df["price"].notna().sum() == 98


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
