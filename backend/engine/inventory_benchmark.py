"""
Inventory analysis benchmark / developer test harness.

Purpose: answer ONE question — how much of inventory runtime is marketplace
retrieval vs. the valuation engine — by running the exact same
`analyze_inventory` -> `evaluate_car` pipeline against two transports:

  * LIVE  (real, polite Phase-6 retrieval), optionally RECORDING every
    normalized pool into a MarketStore, and
  * CACHE-ONLY (zero HTTP), replaying the recorded pools.

It also verifies that cache-only results are byte-identical to the live results
for the same market pools, and reports timing for both.

This harness changes NO valuation logic. It only drives the existing pipeline
with different market-data transports and measures it.

CLI:
    python engine/inventory_benchmark.py --live-groups 12 --synthetic 400

    --dataset       demo fixture name for the base inventory (default: sample = 98 cars)
    --live-groups   how many unique (brand,model) groups to fetch LIVE (populate
                    the cache). 0 = skip live (reuse an existing saved store).
    --synthetic     size of the synthetic cache-only valuation benchmark (0 = skip)
    --delay         polite delay between live requests (seconds, default 1.0)
    --store         path to the saved MarketStore (default: fixtures/market_cache.pkl)
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import OrderedDict

# Allow running both as `python engine/inventory_benchmark.py` and as a module.
sys.path.insert(0, os.path.dirname(__file__))

from inventory_api import parse_demo  # noqa: E402
from inventory_run import analyze_inventory  # noqa: E402
from market_provider import (  # noqa: E402
    DEFAULT_STORE_PATH,
    CachedMarketProvider,
    LiveMarketProvider,
    MarketStore,
    PersistentCacheProvider,
)


# --------------------------------------------------------------------------- #
# Dataset helpers
# --------------------------------------------------------------------------- #
def confirmed_rows(dataset: str) -> list[dict]:
    """Load a demo inventory and keep only rows valid for comparison (as the UI does)."""
    report = parse_demo(dataset)
    return [r for r in report["rows"] if r.get("valid_for_comparison")]


def group_key(row: dict) -> tuple:
    return ((row.get("brand") or "").strip().lower(), (row.get("model") or "").strip().lower())


def limit_to_groups(rows: list[dict], k: int) -> list[dict]:
    """Return the subset of rows belonging to the first k unique (brand,model) groups."""
    if k <= 0:
        return rows
    seen: "OrderedDict[tuple, None]" = OrderedDict()
    for r in rows:
        seen.setdefault(group_key(r), None)
    keep = set(list(seen.keys())[:k])
    return [r for r in rows if group_key(r) in keep]


def synthetic_inventory(store: MarketStore, n: int) -> list[dict]:
    """
    Build an n-car inventory from the (brand,model) groups present in the store,
    so a cache-only run finds a real pool for every car. Specs (year/km/price)
    are varied per car so `evaluate_car` does genuine per-car work.
    """
    models = store.unique_models()
    if not models:
        return []
    fuels = ["diesel", "petrol", "diesel", "hybrid"]
    rows: list[dict] = []
    for i in range(n):
        brand, model = models[i % len(models)]
        rows.append({
            "row_index": i,
            "row_number": i + 1,
            "brand": brand,
            "model": model,
            "variant": None,
            "year": 2014 + (i % 9),           # 2014..2022
            "fuel": fuels[i % len(fuels)],
            "km": 40000 + (i % 20) * 9000,     # 40k..211k
            "price": 6000 + (i % 25) * 900,    # 6.0k..27.6k
            "body_type": None,
        })
    return rows


# --------------------------------------------------------------------------- #
# Pipeline drivers
# --------------------------------------------------------------------------- #
def run(rows: list[dict], provider, **kw) -> tuple[dict, dict]:
    """
    Drain analyze_inventory. Returns (summary_event, results_by_row_index).
    results_by_row_index maps row_index -> the compact per-car result dict.
    """
    summary = None
    results: dict[int, dict] = {}
    for ev in analyze_inventory(rows, provider=provider, **kw):
        if ev["stage"] == "car_done":
            results[ev["car"]["row_index"]] = ev["car"]
        elif ev["stage"] == "summary":
            summary = ev
    return summary, results


# --------------------------------------------------------------------------- #
# Identity verification
# --------------------------------------------------------------------------- #
# Fields that must match exactly between live and cache-only for the same pools.
_COMPARE_FIELDS = [
    "confidence_flag", "confidence_reasons", "rank_source", "rank_price_diff_pct",
    "median_spread_pct", "ab_vs_bz_pct_gap", "missing_critical_fields",
]
_SOURCE_FIELDS = ["median_eur", "price_diff_pct", "comparable_count", "tier", "insufficient"]


def diff_results(live: dict[int, dict], cached: dict[int, dict]) -> list[str]:
    """Return a list of human-readable mismatches (empty == identical)."""
    problems: list[str] = []
    if set(live) != set(cached):
        problems.append(f"row sets differ: live={sorted(live)} cache={sorted(cached)}")
        return problems
    for ri in sorted(live):
        a, b = live[ri], cached[ri]
        for f in _COMPARE_FIELDS:
            if a.get(f) != b.get(f):
                problems.append(f"row {ri}: {f}: live={a.get(f)!r} cache={b.get(f)!r}")
        for src in ("autobazar", "bazos"):
            for f in _SOURCE_FIELDS:
                if a[src].get(f) != b[src].get(f):
                    problems.append(f"row {ri}: {src}.{f}: live={a[src].get(f)!r} cache={b[src].get(f)!r}")
    return problems


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def print_report(title: str, bench: dict) -> None:
    ret = bench["retrieval_time_s"]
    ev = bench["eval_time_s"]
    tot = bench["total_time_s"]
    ret_pct = (ret / tot * 100) if tot else 0.0
    ev_pct = (ev / tot * 100) if tot else 0.0
    per_car_ms = (ev / bench["total_cars"] * 1000) if bench["total_cars"] else 0.0
    print(f"\n=== {title} ({bench['mode']}) ===")
    print(f"  inventory cars ............ {bench['total_cars']}")
    print(f"  unique (brand,model) ...... {bench['unique_groups']}")
    print(f"  cached groups (0 HTTP) .... {bench['cached_groups']}")
    print(f"  market lookups (calls) .... {bench['market_lookups']}")
    print(f"  HTTP requests (network) ... {bench['http_requests']}")
    print(f"  retrieval time ............ {ret:8.3f}s  ({ret_pct:5.1f}% of total)")
    print(f"  valuation (evaluate_car) .. {ev:8.3f}s  ({ev_pct:5.1f}% of total)")
    print(f"  total time ................ {tot:8.3f}s")
    print(f"  valuation per car ......... {per_car_ms:8.2f} ms")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Inventory analysis benchmark")
    ap.add_argument("--dataset", default="sample")
    ap.add_argument("--live-groups", type=int, default=8,
                    help="unique (brand,model) groups to fetch live (0 = reuse saved store)")
    ap.add_argument("--synthetic", type=int, default=400,
                    help="synthetic cache-only inventory size (0 = skip)")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--store", default=DEFAULT_STORE_PATH)
    ap.add_argument("--cache-demo", type=int, default=0,
                    help="unique (brand,model) groups for the cold-vs-warm persistent-cache demo "
                         "(uses the real PersistentCacheProvider; 0 = skip)")
    args = ap.parse_args()

    all_rows = confirmed_rows(args.dataset)
    total_groups = len({group_key(r) for r in all_rows})
    print(f"Dataset '{args.dataset}': {len(all_rows)} comparable cars, "
          f"{total_groups} unique (brand,model) groups.")

    # ---- Phase A: live populate + identity check -------------------------- #
    if args.live_groups > 0:
        live_rows = limit_to_groups(all_rows, args.live_groups)
        n_groups = len({group_key(r) for r in live_rows})
        print(f"\n[LIVE] Fetching {n_groups} group(s) covering {len(live_rows)} car(s), "
              f"delay={args.delay}s ...")
        store = MarketStore()
        live_provider = LiveMarketProvider(delay=args.delay, store=store)
        live_summary, live_results = run(live_rows, live_provider, delay=args.delay)
        print_report(f"LIVE populate ({len(live_rows)} cars)", live_summary["benchmark"])
        if live_summary["disabled_sources"]:
            print(f"  NOTE: sources disabled by circuit breaker: {live_summary['disabled_sources']}")

        path = store.save(args.store)
        print(f"  saved market store -> {path} ({len(store.entries)} pools)")

        # Re-run the SAME rows cache-only and verify identical results.
        print(f"\n[CACHE-ONLY] Replaying the same {len(live_rows)} car(s) with 0 HTTP ...")
        cache_provider = CachedMarketProvider(store)
        cache_summary, cache_results = run(live_rows, cache_provider, delay=args.delay)
        print_report(f"CACHE-ONLY replay ({len(live_rows)} cars)", cache_summary["benchmark"])

        problems = diff_results(live_results, cache_results)
        assert cache_summary["benchmark"]["http_requests"] == 0, "cache-only made HTTP requests!"
        if problems:
            print(f"\n  !! IDENTITY CHECK FAILED ({len(problems)} mismatch(es)):")
            for p in problems[:20]:
                print(f"     - {p}")
            return 1
        print(f"\n  IDENTITY CHECK PASSED: all {len(live_results)} cars identical "
              f"(live vs cache-only), 0 HTTP in cache-only mode.")
    else:
        store = MarketStore.load_or_empty(args.store)
        print(f"\n[LIVE] skipped; loaded saved store ({len(store.entries)} pools).")

    # ---- Phase B: synthetic cache-only valuation benchmark ---------------- #
    if args.synthetic > 0 and store.entries:
        syn = synthetic_inventory(store, args.synthetic)
        print(f"\n[CACHE-ONLY] Synthetic valuation benchmark: {len(syn)} cars "
              f"across {len(store.unique_models())} recorded models (0 HTTP) ...")
        syn_summary, _ = run(syn, CachedMarketProvider(store), delay=0.0)
        print_report(f"SYNTHETIC valuation ({len(syn)} cars)", syn_summary["benchmark"])
        b = syn_summary["benchmark"]
        assert b["http_requests"] == 0, "synthetic cache-only made HTTP requests!"
        print(f"  confidence: HIGH={syn_summary['counts']['high']} "
              f"MED={syn_summary['counts']['medium']} LOW={syn_summary['counts']['low']} "
              f"INSUF={syn_summary['counts']['insufficient']}")

    # ---- Phase C: cold vs warm PERSISTENT cache demo ---------------------- #
    # Uses the real production transport (PersistentCacheProvider) so the numbers
    # reflect exactly what Inventory mode does: first run fetches live and fills
    # the cache; the second identical run reuses it with ZERO HTTP.
    if args.cache_demo > 0:
        import tempfile
        demo_rows = limit_to_groups(all_rows, args.cache_demo)
        n = len({group_key(r) for r in demo_rows})
        demo_path = os.path.join(tempfile.mkdtemp(), "cache_demo.pkl")
        print(f"\n[PERSISTENT CACHE] Cold vs warm over {n} group(s) / {len(demo_rows)} car(s), "
              f"delay={args.delay}s, ttl=24h ...")

        cold = PersistentCacheProvider(path=demo_path, delay=args.delay)
        cold_summary, cold_results = run(demo_rows, cold, delay=args.delay)
        print_report(f"COLD run ({len(demo_rows)} cars, empty cache)", cold_summary["benchmark"])

        # A fresh provider instance LOADS the just-persisted store from disk,
        # simulating a later, separate inventory run by the same dealer.
        warm = PersistentCacheProvider(path=demo_path, delay=args.delay)
        warm_summary, warm_results = run(demo_rows, warm, delay=args.delay)
        print_report(f"WARM run ({len(demo_rows)} cars, reused cache)", warm_summary["benchmark"])

        cb, wb = cold_summary["benchmark"], warm_summary["benchmark"]
        problems = diff_results(cold_results, warm_results)
        assert wb["http_requests"] == 0, "warm run made HTTP requests!"
        speedup = (cb["total_time_s"] / wb["total_time_s"]) if wb["total_time_s"] else float("inf")
        print("\n  --- cold vs warm ---")
        print(f"    HTTP requests ... cold={cb['http_requests']:4d}   warm={wb['http_requests']:4d}   "
              f"(saved {cb['http_requests'] - wb['http_requests']})")
        print(f"    total time ...... cold={cb['total_time_s']:8.3f}s  warm={wb['total_time_s']:8.3f}s  "
              f"({speedup:.0f}x faster)")
        print(f"    identity ........ {'PASS' if not problems else 'FAIL (%d mismatch)' % len(problems)}")
        if problems:
            for p in problems[:20]:
                print(f"       - {p}")
            return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
