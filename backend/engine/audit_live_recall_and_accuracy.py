"""
Live recall + parsing-accuracy audit.

Answers two questions directly, using code we already trust (no browser
automation -- see conversation: both Bazos and Autobazar already put full
structured data in the raw HTTP response our scrapers read, so a headless
browser would see nothing new):

  1. RECALL: for a given (brand, model), does our database actually contain
     every listing that's live on the site right now? A gap is either pure
     backlog (genuinely not captured yet) or a REACHABILITY bug (captured,
     but MarketCollectorProvider can't find it -- the same shape of bug as
     the Bazos classification gap / Autobazar brand-spelling gap fixed
     2026-08-28).
  2. ACCURACY: for listings we DO have, does a fresh live re-parse of the
     same page agree with what's stored (year/km/fuel/price)? A mismatch
     means either the ad genuinely changed since capture (expected, not a
     bug) or our extractor has a bug (the fuel/year regex bugs fixed
     2026-08-28/29 were both found exactly this way, just manually).

Reuses retrieve_bazos / retrieve_autobazar (phase6_validate.py) UNCHANGED --
the same live-fetch functions the engine itself uses for the single-car
path, so this audit can never diverge from production behavior. Makes real,
polite, delay-paced network requests -- deliberately NOT part of the
offline test suite or CI; run by hand for a spot-check.

Usage:
    python3 audit_live_recall_and_accuracy.py --brand Skoda --model Octavia
    python3 audit_live_recall_and_accuracy.py --from-csv "/path/to/inventory.csv" --sample 6
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time

import pandas as pd

from inventory_normalizer import normalize_inventory
from phase6_validate import (
    InvCar, retrieve_autobazar, retrieve_bazos, rule_brand, rule_model_soft, MATCH,
)
from market_provider import MarketCollectorProvider, MARKET_COLLECTOR_DB_PATH

DELAY = 1.5
MAX_PAGES = 2

_RAW_TABLE = {"bazos": "bazos_listings", "autobazar": "autobazar_listings"}


def _minimal_car(brand: str, model: str) -> InvCar:
    """A bare (brand, model) car -- retrieve_* only queries by these two
    fields, everything else is used later for tier matching, not retrieval."""
    return InvCar(
        row_index=-1, brand=brand, model=model, variant_engine=None, fuel=None,
        year=None, km=None, price=None, power_kw=None, transmission=None,
        body_type=None, variant_raw=None, power_source="missing",
    )


def _raw_captured_row(conn, source: str, listing_id: str):
    table = _RAW_TABLE[source]
    row = conn.execute(
        f"SELECT year, km, fuel, price FROM {table} WHERE listing_id = ? "
        f"ORDER BY observed_at DESC LIMIT 1",
        (listing_id,),
    ).fetchone()
    return row  # (year, km, fuel, price) or None


def _sample_pairs(csv_path: str, n: int) -> list[tuple[str, str]]:
    report = normalize_inventory(csv_path)
    valid = report.rows[report.rows["valid_for_comparison"]]
    pairs = list(dict.fromkeys(zip(valid["brand"], valid["model"])))  # dedupe, keep order
    return pairs[:n]


def _is_genuinely_this_car(car: InvCar, row) -> bool:
    """Same brand+model check the real engine applies (rule_brand /
    rule_model_soft) BEFORE anything is trusted as a genuine recall gap.
    Bazos's own live search is loose free-text -- searching "Seat Ibiza"
    routinely returns a Mazda 2, wheel/tire ads, and other off-topic
    listings that were never going to be reachable via an Ibiza query for
    the correct reason: they're not an Ibiza. Without this filter those
    show up as false "possible bugs" (confirmed 2026-08-29: the first,
    unfiltered version of this script reported 403 "unreachable" across a
    27-model run, the large majority of which were exactly this kind of
    noise, not real gaps)."""
    return (rule_brand(car, row) == MATCH) and (rule_model_soft(car, row) == MATCH)


def audit_one(source: str, brand: str, model: str, conn, provider: MarketCollectorProvider) -> dict:
    car = _minimal_car(brand, model)
    fetch_fn = retrieve_bazos if source == "bazos" else retrieve_autobazar
    live_df, err = fetch_fn(car, MAX_PAGES, DELAY)

    live_ids = set(live_df["listing_id"].dropna()) if not live_df.empty else set()
    stored_ids = set()
    if not live_ids:
        return {"live_count": 0, "err": err, "not_captured": [], "unreachable": [],
                "accuracy_mismatches": [], "noise": 0, "checked": 0}

    provider_res = provider.retrieve(source, car, pages=MAX_PAGES, deadline=None)
    if not provider_res.df.empty and "listing_id" in provider_res.df.columns:
        stored_ids = set(provider_res.df["listing_id"].dropna())

    not_captured, unreachable, mismatches = [], [], []
    noise = 0
    for _, row in live_df.iterrows():
        lid = row.get("listing_id")
        if lid is None:
            continue
        if not _is_genuinely_this_car(car, row):
            noise += 1
            continue
        raw = _raw_captured_row(conn, source, str(lid))
        if raw is None:
            not_captured.append((lid, row.get("title")))
            continue
        if lid not in stored_ids:
            unreachable.append((lid, row.get("title")))
            continue
        # Accuracy check: fresh live parse vs. most recent stored capture.
        stored_year, stored_km, stored_fuel, stored_price = raw
        live_year = row.get("year")
        live_year = None if pd.isna(live_year) else live_year
        diffs = []
        if live_year is not None and stored_year is not None and int(live_year) != int(stored_year):
            diffs.append(f"year live={live_year} stored={stored_year}")
        if row.get("fuel") and stored_fuel and str(row["fuel"]) != str(stored_fuel):
            diffs.append(f"fuel live={row['fuel']!r} stored={stored_fuel!r}")
        if diffs:
            mismatches.append((lid, row.get("title"), diffs))

    return {
        "live_count": len(live_ids), "err": err,
        "not_captured": not_captured, "unreachable": unreachable,
        "accuracy_mismatches": mismatches, "noise": noise, "checked": len(live_ids),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand")
    ap.add_argument("--model")
    ap.add_argument("--from-csv")
    ap.add_argument("--sample", type=int, default=6)
    args = ap.parse_args()

    if args.brand and args.model:
        pairs = [(args.brand, args.model)]
    elif args.from_csv:
        pairs = _sample_pairs(args.from_csv, args.sample)
    else:
        print("Pass --brand/--model or --from-csv", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"file:{MARKET_COLLECTOR_DB_PATH}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout = 15000")
    provider = MarketCollectorProvider()

    totals = {"live": 0, "not_captured": 0, "unreachable": 0, "mismatches": 0, "noise": 0}
    disabled: set[str] = set()
    consec_blocks = {"bazos": 0, "autobazar": 0}
    for brand, model in pairs:
        print(f"\n=== {brand} {model} ===")
        for source in ("bazos", "autobazar"):
            if source in disabled:
                print(f"  [{source}] skipped (disabled after repeated blocks)")
                continue
            res = audit_one(source, brand, model, conn, provider)
            blocked = bool(res.get("err")) and "BLOCKED" in res["err"]
            consec_blocks[source] = consec_blocks[source] + 1 if blocked else 0
            if consec_blocks[source] >= 2:
                disabled.add(source)
                print(f"  [{source}] BLOCKED twice in a row -- disabling {source} for "
                      f"the rest of this run (no circumvention attempted)")
            print(f"  [{source}] live={res['live_count']}  (noise/off-topic: {res.get('noise', 0)})"
                  + (f"  err={res['err']}" if res.get("err") else ""))
            totals["live"] += res["live_count"]
            totals["not_captured"] += len(res["not_captured"])
            totals["unreachable"] += len(res["unreachable"])
            totals["mismatches"] += len(res["accuracy_mismatches"])
            totals["noise"] += res.get("noise", 0)
            for lid, title in res["not_captured"][:5]:
                print(f"    NOT CAPTURED  [{lid}] {title!r}")
            for lid, title in res["unreachable"][:5]:
                print(f"    UNREACHABLE   [{lid}] {title!r}  <-- captured but provider can't find it, possible bug")
            for lid, title, diffs in res["accuracy_mismatches"][:5]:
                print(f"    MISMATCH      [{lid}] {title!r}  {diffs}")
            time.sleep(DELAY)

    print(f"\n=== TOTALS ===")
    print(f"live listings checked: {totals['live']}")
    print(f"  of which off-topic search noise (not this brand/model, excluded above): {totals['noise']}")
    print(f"genuinely this car, not yet captured (backlog): {totals['not_captured']}")
    print(f"genuinely this car, captured but UNREACHABLE (possible bug): {totals['unreachable']}")
    print(f"accuracy mismatches (live vs stored year/fuel, this car only): {totals['mismatches']}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
