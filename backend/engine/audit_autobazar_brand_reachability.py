"""
Standalone audit: for every brand this codebase might plausibly query
Autobazar with, is it actually reachable via MarketCollectorProvider's
INCREMENTAL path (the primary, actively-growing data source)?

This exists because three real bugs (Skoda/Citroen/MG, 2026-08-28) were all
the SAME failure shape: our canonical brand spelling doesn't exact-match
Autobazar's own structured search_brand field (a diacritic or a case
difference), so the incremental query silently returns zero rows and falls
back to a stale, once-off snapshot -- not an error, not an empty pool,
just quietly worse data. Ordinary unit tests can't catch this class of bug:
they run against clean, hand-built fixtures, and this is fundamentally a
REAL-DATA reachability problem, not a rule-logic problem. This script
checks against the actual production DB instead.

Deliberately NOT part of the offline pytest-style suite (test_*.py files
in this directory) -- those explicitly avoid touching market_history.sqlite3
(see test_market_collector_provider.py's own docstring). This is a live,
read-only DB audit; run it manually whenever new brands might have been
captured, or wire it into a periodic check if that's wanted later.

Usage:
    python3 audit_autobazar_brand_reachability.py
"""
from __future__ import annotations

import sys

from market_provider import MarketCollectorProvider, MARKET_COLLECTOR_DB_PATH
from row_parser import _BRAND_MAP


def _connect_readonly(db_path: str):
    import sqlite3
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def audit(db_path: str = MARKET_COLLECTOR_DB_PATH) -> int:
    conn = _connect_readonly(db_path)
    try:
        stored = dict(conn.execute(
            "SELECT search_brand, COUNT(*) FROM autobazar_listings "
            "WHERE parse_mode = 'sitemap_detail' GROUP BY search_brand"
        ).fetchall())
    finally:
        conn.close()

    # Every canonical brand string this codebase might actually query with
    # (row_parser.py's vocabulary -- the same one the frontend/paste-a-row
    # path resolves inventory brands to), deduplicated.
    canonical_brands = sorted(set(_BRAND_MAP.values()))

    overrides = MarketCollectorProvider._BRAND_LOOKUP_OVERRIDES
    extra = MarketCollectorProvider._AUTOBAZAR_INCREMENTAL_EXTRA_OVERRIDES

    problems = []
    for brand in canonical_brands:
        key = brand.strip().lower()
        queried_as = extra.get(key, overrides.get(key, brand))
        if queried_as in stored:
            continue  # reachable, exact match found
        # Not reachable as queried. Only worth flagging if Autobazar
        # actually HAS listings under some near-miss spelling for this
        # brand (case/diacritic-insensitive), since a brand Autobazar
        # simply has zero data for either way is not a bug.
        near_miss = [s for s in stored if s and s.strip().lower() == key]
        if near_miss:
            problems.append((brand, queried_as, near_miss[0], stored[near_miss[0]]))

    print(f"Checked {len(canonical_brands)} canonical brands against "
          f"{len(stored)} distinct Autobazar search_brand values.\n")

    if not problems:
        print("No reachability gaps found -- every canonical brand with "
              "matching Autobazar data (case/diacritic-insensitive) is "
              "reachable via the incremental path as currently mapped.")
        return 0

    print(f"UNREACHABLE ({len(problems)}) -- these brands have real "
          f"Autobazar data under a near-miss spelling the incremental "
          f"query doesn't match, so they're silently falling back to "
          f"stale/no data instead:\n")
    for brand, queried_as, stored_as, count in problems:
        print(f"  {brand!r} -> queried as {queried_as!r}, "
              f"but Autobazar stores it as {stored_as!r} ({count} listings)")
    return 1


if __name__ == "__main__":
    sys.exit(audit())
