"""
Standalone audit: for every captured listing (both sources), does the title
contain a strong fuel-type signal (plug-in/PHEV, LPG, CNG) that the
STRUCTURED fuel field's own reading doesn't reflect, even after
phase5_compare.normalize_fuel_with_title_fallback runs?

This exists because that exact gap (title says "Plug-in Hybrid", structured
field says generic "Hybrid") was found live on 2026-08-28 for a Hyundai
Ioniq, then confirmed systemic across 5+ brands, then found AGAIN for LPG/
CNG retrofits (title says "1.6 LPG", field says base "Petrol") -- both fixed
in normalize_fuel_with_title_fallback. This script re-runs that same check
against the WHOLE dataset so a remaining or future gap (a keyword variant
the fallback doesn't recognize yet, a new pattern on a brand not yet seen)
surfaces on its own instead of waiting for someone to notice a missing car.

Deliberately NOT part of the offline pytest-style suite -- reads the real
market_history.sqlite3, same reasoning as the other audit_*.py scripts here.

Usage:
    python3 audit_fuel_title_consistency.py
"""
from __future__ import annotations

import sys

from market_provider import MARKET_COLLECTOR_DB_PATH
from phase5_compare import normalize_fuel_with_title_fallback

# Signal -> (title regex source already used in the fallback, expected fuel)
# Kept as plain substrings here (not the compiled regexes) so this script
# stays a simple, readable cross-check rather than importing private state.
_SIGNALS = [
    ("plug", "PHEV"),
    ("phev", "PHEV"),
    ("lpg", "LPG"),
    ("cng", "CNG"),
]


def audit(db_path: str = MARKET_COLLECTOR_DB_PATH) -> int:
    import sqlite3
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout = 15000")

    gaps: list[tuple[str, str, str, str]] = []
    checked = 0
    try:
        for source, table in (("bazos", "bazos_listings"), ("autobazar", "autobazar_listings")):
            rows = conn.execute(f"SELECT title, fuel FROM {table}").fetchall()
            for title, raw_fuel in rows:
                if not title:
                    continue
                checked += 1
                tl = title.lower()
                for keyword, expected in _SIGNALS:
                    if keyword not in tl:
                        continue
                    resolved = normalize_fuel_with_title_fallback(raw_fuel, title)
                    if resolved != expected:
                        gaps.append((source, title, raw_fuel, resolved))
                    break  # one signal match per row is enough to test it
    finally:
        conn.close()

    print(f"Checked {checked} rows (both sources) for a fuel title/field mismatch "
          f"the current fallback doesn't already resolve.\n")

    if not gaps:
        print("No gaps found -- every title-signaled PHEV/LPG/CNG car resolves "
              "correctly through normalize_fuel_with_title_fallback.")
        return 0

    print(f"GAPS ({len(gaps)}) -- title signals a specific fuel type the "
          f"resolved reading still doesn't match:\n")
    for source, title, raw_fuel, resolved in gaps[:40]:
        print(f"  [{source}] {title!r} (raw fuel {raw_fuel!r}) -> resolved {resolved!r}")
    if len(gaps) > 40:
        print(f"  ... and {len(gaps) - 40} more")
    return 1


if __name__ == "__main__":
    sys.exit(audit())
