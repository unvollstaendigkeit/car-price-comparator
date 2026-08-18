"""
Direct, offline tests for evaluate_car() against the ACTUAL production
backend/engine/phase7_fullrun.py (no separate/legacy copy involved).

Covers: sources stay independent (never merged into one price), median_spread
is computed correctly, the primary-source rank tie-break, and per-source vs.
overall insufficient-data behavior. No network access: all market data is
synthetic.

Run: python test_evaluate_car.py
  or: python -m pytest test_evaluate_car.py -q
"""
from __future__ import annotations

import pandas as pd

from phase6_validate import InvCar
from phase7_fullrun import evaluate_car, median_spread_pct

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


def _rows(n: int, source: str, prices: list[int], **field_overrides) -> pd.DataFrame:
    assert len(prices) == n
    rows = []
    for i in range(n):
        row = {
            "source": source,
            "listing_id": f"{source}-{i}",
            "title": "Skoda Octavia 2.0 TDI",
            "year": 2020,
            "km": 80_000,
            "variant_engine": "2.0 TDI",
            "power_kw": 110,
            "transmission": "Manual",
            "fuel": "Diesel",
            "body_type": "Combi",
            "price": prices[i],
            "url": f"https://example.test/{source}/{i}",
        }
        row.update(field_overrides)
        rows.append(row)
    return pd.DataFrame(rows, columns=_MARKET_COLS)


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=_MARKET_COLS)


# --------------------------------------------------------------------------- #
# Sources stay independent -- never merged into one price
# --------------------------------------------------------------------------- #
def test_sources_remain_separate_never_merged():
    car = _car()
    ab_df = _rows(4, "autobazar", [20_000, 20_000, 20_000, 20_000])
    bz_df = _rows(4, "bazos", [17_000, 17_000, 17_000, 17_000])
    row, ab_est, bz_est, ab_matched, bz_matched = evaluate_car(car, ab_df, bz_df, "", "")

    assert row["ab_median_eur"] == 20_000
    assert row["bz_median_eur"] == 17_000
    assert row["ab_median_eur"] != row["bz_median_eur"]
    # Independently returned estimate dicts, not blended into one.
    assert ab_est["market_median"] == 20_000
    assert bz_est["market_median"] == 17_000
    # No merged/blended single-price key anywhere in the flat summary row.
    assert "market_price" not in row
    assert "median_eur" not in row  # only the ab_/bz_-prefixed variants exist
    assert not any("blend" in str(k).lower() for k in row)


# --------------------------------------------------------------------------- #
# median_spread_pct is computed correctly (and matches the standalone helper)
# --------------------------------------------------------------------------- #
def test_median_spread_calculated_correctly():
    car = _car()
    ab_df = _rows(4, "autobazar", [20_000, 20_000, 20_000, 20_000])
    bz_df = _rows(4, "bazos", [17_000, 17_000, 17_000, 17_000])
    row, ab_est, bz_est, _, _ = evaluate_car(car, ab_df, bz_df, "", "")

    expected = round(median_spread_pct(ab_est["market_median"], bz_est["market_median"]), 1)
    assert expected == 15.0  # (20000-17000)/20000*100
    assert row["median_spread_pct"] == expected


def test_median_spread_none_when_a_source_has_no_median():
    car = _car()
    ab_df = _rows(4, "autobazar", [20_000, 20_000, 20_000, 20_000])
    row, ab_est, bz_est, _, _ = evaluate_car(car, ab_df, _empty(), "", "")
    assert bz_est["market_median"] is None
    assert row["median_spread_pct"] is None


# --------------------------------------------------------------------------- #
# rank_source tie-break: Autobazar wins ties, otherwise more comparables wins
# --------------------------------------------------------------------------- #
def test_rank_source_autobazar_wins_tie():
    car = _car()
    ab_df = _rows(4, "autobazar", [20_000, 20_500, 21_000, 20_800])
    bz_df = _rows(4, "bazos", [17_000, 17_200, 17_400, 17_600])
    row, ab_est, bz_est, _, _ = evaluate_car(car, ab_df, bz_df, "", "")
    assert ab_est["comparable_count"] == bz_est["comparable_count"] == 4
    assert row["rank_source"] == "autobazar"
    assert row["rank_price_diff_pct"] == ab_est["undervaluation_pct"]


def test_rank_source_bazos_wins_when_strictly_more_comparables():
    car = _car()
    ab_df = _rows(4, "autobazar", [20_000, 20_500, 21_000, 20_800])
    bz_df = _rows(6, "bazos", [17_000, 17_200, 17_400, 17_600, 17_100, 17_300])
    row, ab_est, bz_est, _, _ = evaluate_car(car, ab_df, bz_df, "", "")
    assert bz_est["comparable_count"] > ab_est["comparable_count"]
    assert row["rank_source"] == "bazos"
    assert row["rank_price_diff_pct"] == bz_est["undervaluation_pct"]
    # The ranking driver is an explicit source pick, never an average of both.
    avg_guess = (ab_est["undervaluation_pct"] + bz_est["undervaluation_pct"]) / 2
    assert row["rank_price_diff_pct"] != avg_guess


# --------------------------------------------------------------------------- #
# Insufficient-data behavior, as documented in confidence_flag()
# --------------------------------------------------------------------------- #
def test_insufficient_flag_when_both_sources_insufficient():
    car = _car()
    row, ab_est, bz_est, _, _ = evaluate_car(car, _empty(), _empty(), "", "")
    assert ab_est["comparable_count"] == 0
    assert bz_est["comparable_count"] == 0
    assert row["confidence_flag"] == "INSUFFICIENT"


def test_per_source_insufficient_flags_when_only_one_source_short():
    car = _car()
    ab_df = _rows(4, "autobazar", [20_000, 20_500, 21_000, 20_800])
    row, ab_est, bz_est, _, _ = evaluate_car(car, ab_df, _empty(), "", "")
    assert row["ab_insufficient"] is False
    assert row["bz_insufficient"] is True
    # One usable source is enough to avoid the overall INSUFFICIENT flag.
    assert row["confidence_flag"] != "INSUFFICIENT"


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
