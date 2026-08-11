"""
Regression tests for the single-car execution path.

Central guarantee: a car described through `SingleCarInput` is normalized into
the EXACT same canonical `InvCar` as the corresponding inventory row, and run
through the EXACT same comparison engine (`evaluate_car`). So single-car mode
and inventory mode must produce identical numbers on identical market data.

These tests are fully offline: market data is synthetic and fed to both modes,
isolating the single-car input path from live-retrieval nondeterminism.
"""
from __future__ import annotations

import dataclasses

import pandas as pd

from phase6_validate import load_inventory, to_invcar, InvCar
from phase7_fullrun import evaluate_car
from single_car import SingleCarInput, build_canonical_car, compare_single_car

SAMPLE = "inventory_sample.csv"
# Fields that must be identical between inventory-mode and single-car-mode cars.
# row_index is intentionally excluded (single-car uses a sentinel index).
_COMPARE_FIELDS = [
    "brand", "model", "variant_engine", "fuel", "year", "km", "price",
    "power_kw", "transmission", "body_type", "variant_raw", "power_source",
]

_MARKET_COLS = ["source", "listing_id", "title", "variant_engine", "fuel",
                "year", "km", "power_kw", "transmission", "body_type",
                "price", "url"]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _pick_cars(n: int = 8) -> list[pd.Series]:
    """Pick n diverse cars that have a price (needed for undervaluation)."""
    inv = load_inventory(SAMPLE)
    inv = inv[inv["price"].notna()]
    # spread across fuels + a couple of premium German brands for model-name stress
    picks: list[pd.Series] = []
    seen: set[int] = set()

    def take(mask):
        sub = inv[mask & ~inv["row_index"].isin(seen)]
        if not sub.empty:
            r = sub.iloc[0]
            seen.add(int(r["row_index"]))
            picks.append(r)

    take(inv["fuel"] == "Diesel")
    take(inv["fuel"] == "Petrol")
    take(inv["fuel"] == "Hybrid")
    take(inv["fuel"] == "PHEV")
    take(inv["fuel"] == "Electric")
    take(inv["brand"].isin(["BMW", "Audi", "Mercedes"]))
    for _, r in inv.iterrows():
        if len(picks) >= n:
            break
        if int(r["row_index"]) not in seen:
            seen.add(int(r["row_index"]))
            picks.append(r)
    return picks[:n]


def _input_from_row(row: pd.Series) -> SingleCarInput:
    """
    Reconstruct the single-car input from an inventory row's ORIGINAL fields.
    power_kw / transmission are intentionally omitted so they are re-derived
    from `variant`, exactly as the inventory loader does.
    """
    def val(k):
        v = row.get(k)
        return None if v is None or (isinstance(v, float) and pd.isna(v)) else v

    return SingleCarInput(
        brand=str(row["brand"]),
        model=str(row["model"]),
        variant=val("variant_raw"),
        year=val("year"),
        fuel=val("fuel"),
        km=val("km"),
        price=val("price"),
        body_type=val("body_type"),
    )


def _synthetic_market(car: InvCar, source: str, prices: list[int]) -> pd.DataFrame:
    """Build comparable listings cloned from the car's spec (match at STRICT)."""
    rows = []
    for i, p in enumerate(prices):
        rows.append({
            "source": source,
            "listing_id": f"{source}-{i}",
            "title": f"{car.brand} {car.model} {car.variant_raw or ''}".strip(),
            "variant_engine": car.variant_engine,
            "fuel": car.fuel,
            "year": car.year,
            "km": car.km,
            "power_kw": car.power_kw,
            "transmission": car.transmission,
            "body_type": car.body_type,
            "price": p,
            "url": f"https://example.test/{source}/{i}",
        })
    return pd.DataFrame(rows, columns=_MARKET_COLS)


# --------------------------------------------------------------------------- #
# Canonical-car equivalence
# --------------------------------------------------------------------------- #
def test_single_car_builds_identical_invcar():
    mismatches = []
    for row in _pick_cars():
        inv_car = to_invcar(row)
        sc_car = build_canonical_car(_input_from_row(row))
        for f in _COMPARE_FIELDS:
            if getattr(inv_car, f) != getattr(sc_car, f):
                mismatches.append(
                    f"#{row['row_index']} {inv_car.brand} {inv_car.model}: "
                    f"{f} inv={getattr(inv_car, f)!r} single={getattr(sc_car, f)!r}"
                )
    assert not mismatches, "InvCar field mismatches:\n" + "\n".join(mismatches)


# --------------------------------------------------------------------------- #
# Identical engine output on identical market data
# --------------------------------------------------------------------------- #
def test_single_car_matches_inventory_mode_numbers():
    diffs = []
    for row in _pick_cars():
        inv_car = to_invcar(row)
        price = inv_car.price
        # Market priced ~15% above asking so both sources read "below market".
        ab_prices = [round(price * m) for m in (1.10, 1.15, 1.18, 1.20, 1.12)]
        bz_prices = [round(price * m) for m in (1.08, 1.14, 1.16, 1.22)]
        ab_df = _synthetic_market(inv_car, "autobazar", ab_prices)
        bz_df = _synthetic_market(inv_car, "bazos", bz_prices)

        inv_row, ab_est, bz_est, _, _ = evaluate_car(inv_car, ab_df, bz_df, "", "")
        result = compare_single_car(
            _input_from_row(row), retrieved=(ab_df, "", bz_df, "")
        )

        checks = {
            "ab_count": (ab_est["comparable_count"],
                         result["sources"]["autobazar"]["comparable_count"]),
            "ab_median": (ab_est["market_median"],
                          result["sources"]["autobazar"]["median_asking_eur"]),
            "ab_pct": (ab_est["undervaluation_pct"],
                       result["sources"]["autobazar"]["undervaluation_pct"]),
            "bz_pct": (bz_est["undervaluation_pct"],
                       result["sources"]["bazos"]["undervaluation_pct"]),
            "flag": (inv_row["confidence_flag"], result["confidence"]["flag"]),
        }
        for name, (a, b) in checks.items():
            if a != b:
                diffs.append(f"#{row['row_index']} {name}: inv={a!r} single={b!r}")
    assert not diffs, "inventory vs single-car differences:\n" + "\n".join(diffs)


# --------------------------------------------------------------------------- #
# Undervaluation sign convention (requirement #5)
# --------------------------------------------------------------------------- #
def test_undervaluation_sign_convention():
    inp = SingleCarInput(brand="Skoda", model="Octavia",
                         variant="2.0 TDI 150HP", year=2020, fuel="Diesel",
                         km=90000, price=10000)
    car = build_canonical_car(inp)
    # Market clearly ABOVE asking -> car is cheap -> positive undervaluation.
    ab = _synthetic_market(car, "autobazar", [14000, 15000, 16000, 15500])
    bz = _synthetic_market(car, "bazos", [14500, 15200, 15800, 16000])
    res = compare_single_car(inp, retrieved=(ab, "", bz, ""))
    a = res["sources"]["autobazar"]
    assert a["undervaluation_pct"] > 0
    # exact formula: (median - asking)/asking*100
    assert a["undervaluation_pct"] == round(
        (a["median_asking_eur"] - 10000) / 10000 * 100, 1
    )
    # And an expensive car reads negative.
    ab2 = _synthetic_market(car, "autobazar", [7000, 7500, 8000, 7800])
    bz2 = _synthetic_market(car, "bazos", [7200, 7600, 7900, 8100])
    res2 = compare_single_car(inp, retrieved=(ab2, "", bz2, ""))
    assert res2["sources"]["autobazar"]["undervaluation_pct"] < 0


# --------------------------------------------------------------------------- #
# Sources kept separate
# --------------------------------------------------------------------------- #
def test_sources_are_never_merged():
    inp = SingleCarInput(brand="Toyota", model="RAV4", variant="2.5 HSD 218HP",
                         year=2021, fuel="Hybrid", km=60000, price=30000)
    car = build_canonical_car(inp)
    # Deliberately divergent medians per source.
    ab = _synthetic_market(car, "autobazar", [40000, 41000, 42000, 41500])
    bz = _synthetic_market(car, "bazos", [33000, 33500, 34000, 33800])
    res = compare_single_car(inp, retrieved=(ab, "", bz, ""))
    ab_med = res["sources"]["autobazar"]["median_asking_eur"]
    bz_med = res["sources"]["bazos"]["median_asking_eur"]
    assert ab_med != bz_med  # each reported independently
    # No merged/blended market price key anywhere in the result.
    assert "market_price" not in res
    assert "blended" not in str(res.keys())
    # Cross-source spread is surfaced, not collapsed into one number.
    assert res["cross_source"]["median_spread_pct"] is not None


# --------------------------------------------------------------------------- #
# Insufficient-data handling
# --------------------------------------------------------------------------- #
def test_insufficient_when_no_comparables():
    inp = SingleCarInput(brand="Toyota", model="RAV4", variant="2.5 HSD",
                         year=2021, fuel="Hybrid", km=60000, price=30000)
    empty = pd.DataFrame(columns=_MARKET_COLS)
    res = compare_single_car(inp, retrieved=(empty, "", empty, ""))
    assert res["insufficient"] is True
    assert res["confidence"]["flag"] == "INSUFFICIENT"
    assert res["sources"]["autobazar"]["comparable_count"] == 0
    assert res["sources"]["bazos"]["comparable_count"] == 0


def test_retrieval_error_surfaced_as_warning():
    inp = SingleCarInput(brand="BMW", model="2 Series", variant="220d",
                         year=2019, fuel="Diesel", km=80000, price=22000)
    empty = pd.DataFrame(columns=_MARKET_COLS)
    res = compare_single_car(
        inp, retrieved=(empty, "BLOCKED: 403 challenge", empty, "")
    )
    assert res["sources"]["autobazar"]["retrieval_error"] == "BLOCKED: 403 challenge"
    assert any("retrieval" in w for w in res["confidence"]["warnings"])


# --------------------------------------------------------------------------- #
# Explicit overrides
# --------------------------------------------------------------------------- #
def test_explicit_power_override_marks_source():
    inp = SingleCarInput(brand="VW", model="Golf", variant="GTI", year=2019,
                         fuel="Petrol", km=70000, price=20000, power_kw=180)
    car = build_canonical_car(inp)
    assert car.power_kw == 180
    assert car.power_source == "explicit"


def test_comparables_include_urls():
    inp = SingleCarInput(brand="Skoda", model="Octavia", variant="2.0 TDI 150HP",
                         year=2020, fuel="Diesel", km=90000, price=10000)
    car = build_canonical_car(inp)
    ab = _synthetic_market(car, "autobazar", [14000, 15000, 16000, 15500])
    res = compare_single_car(inp, retrieved=(ab, "", pd.DataFrame(columns=_MARKET_COLS), ""))
    comps = res["sources"]["autobazar"]["comparables"]
    assert comps and all(c["url"].startswith("https://") for c in comps)
    assert all("price" in c for c in comps)


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {t.__name__}\n     {e}")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} single-car tests passed")
