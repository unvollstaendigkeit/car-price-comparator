"""
Phase 2A: dynamic Autobazar.eu candidate retrieval for ONE inventory car.

Goal (deliberately limited)
---------------------------
Given a car specification (brand / model / variant / year / fuel / km / price),
retrieve a *broad but still relevant* set of currently-advertised Autobazar.eu
listings that could POTENTIALLY be comparable.

This module does NOT decide comparability and does NOT estimate market value.
It only retrieves a candidate set. Scoring / matching / valuation come later.

Search strategy (evidence-based, see README notes)
--------------------------------------------------
Inspecting the live site showed three ways to search, with these totals for the
Kodiaq example:

    keyword "Škoda Kodiaq 2.0 TDI"  -> 656 results, top pages 100% Diesel
    keyword "Škoda Kodiaq diesel"   -> 810 results, 100% Diesel
    path    skoda/kodiaq (all)      -> 1011 results, mixed petrol + diesel

We use the free-text KEYWORD search built from `Brand Model Variant`
(e.g. "Škoda Kodiaq 2.0 TDI"). This keeps the core vehicle identity specific
while letting the site's own relevance ranking allow natural variation in year,
mileage and trim — which is exactly the breadth we want for a candidate pool.
We deliberately do NOT add year/mileage/exact-trim constraints here.

Pagination
----------
Autobazar paginates with `?page=N` (confirmed; the page exposes `maxPage`).
We retrieve up to `max_pages` (default 5), stop early when a page returns no
rows or the site's own `maxPage` is reached, keep a small delay between
requests, and keep total request volume low.

Anti-bot
--------
This module reuses the Phase 1 `fetch()`, which STOPS and reports if the site
returns 401/403/429/5xx, sets an x-bot-request flag, or shows a CAPTCHA/
Cloudflare challenge. Nothing here bypasses any anti-bot mechanism.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field

# Reuse the Phase 1 primitives (single source of truth for fetch/parse/normalise).
from autobazar_scraper import (
    BASE,
    BlockedError,
    OUTPUT_COLUMNS,
    _extract_next_data,
    _record_to_row,
    build_search_url,
    fetch,
    to_dataframe,
)

# Phase 2A output = Phase 1 columns + provenance of each row.
OUTPUT_COLUMNS_2A = OUTPUT_COLUMNS + ["search_query", "search_page"]


@dataclass
class CarSpec:
    """One inventory car. Only brand/model/variant drive the search query today;
    year/fuel/km/price are captured for later phases (matching/valuation) and for
    the human-readable report, but intentionally do NOT narrow the search yet."""

    brand: str
    model: str
    variant: str | None = None
    year: int | None = None
    fuel: str | None = None
    km: int | None = None
    price: int | None = None

    def keyword(self) -> str:
        """Build the search keyword: 'Brand Model Variant'.

        Core identity is specific (brand + model + variant); we do NOT append
        year / mileage / exact trim so comparable cars with reasonable variation
        are still returned.
        """
        parts = [self.brand, self.model]
        if self.variant:
            parts.append(self.variant)
        return " ".join(p.strip() for p in parts if p and p.strip())


@dataclass
class SearchOutcome:
    query: str
    pages_requested: int
    total_reported: int | None
    max_page_site: int | None
    rows: list[dict] = field(default_factory=list)
    page_urls: list[str] = field(default_factory=list)


def _parse_page(html: str) -> tuple[list[dict], int | None, int | None]:
    """Return (rows, total_reported, max_page) from one result page.

    Rows come from the embedded Next.js JSON (same resilient path as Phase 1).
    `total` and `maxPage` let us stop pagination sensibly.
    """
    data = _extract_next_data(html)
    if not data:
        return [], None, None
    try:
        pp = data["props"]["pageProps"]
    except (KeyError, TypeError):
        return [], None, None

    records = (pp.get("searchRecords") or {}).get("data")
    rows = [_record_to_row(r) for r in records] if isinstance(records, list) else []
    total = (pp.get("searchRecords") or {}).get("total")
    max_page = pp.get("maxPage")
    return rows, total, max_page


def search_candidates(
    spec: CarSpec,
    max_pages: int = 5,
    delay: float = 1.5,
) -> SearchOutcome:
    """Retrieve a broad candidate set for one car across up to `max_pages` pages.

    Stops early when: a page returns no rows, or the site's `maxPage` is reached.
    Deduplicates by listing URL. Tags every row with `search_query`/`search_page`.
    """
    query = spec.keyword()
    outcome = SearchOutcome(
        query=query, pages_requested=0, total_reported=None, max_page_site=None
    )
    seen_urls: set[str] = set()

    for page in range(1, max_pages + 1):
        url = build_search_url(keyword=query, page=page)
        if page > 1 and delay:
            time.sleep(delay)  # keep request volume gentle

        html = fetch(url)  # raises BlockedError on any block/challenge
        rows, total, max_page = _parse_page(html)

        outcome.pages_requested = page
        outcome.page_urls.append(url)
        if total is not None:
            outcome.total_reported = total
        if max_page is not None:
            outcome.max_page_site = max_page

        if not rows:
            break  # no more results -> stop

        for row in rows:
            u = row.get("url")
            if u and u in seen_urls:
                continue  # skip duplicates that can appear across pages
            if u:
                seen_urls.add(u)
            row["search_query"] = query
            row["search_page"] = page
            outcome.rows.append(row)

        if max_page is not None and page >= max_page:
            break  # reached the site's last page -> stop

    return outcome


def to_dataframe_2a(rows: list[dict]):
    import pandas as pd

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS_2A)


# --------------------------------------------------------------------------- #
# CLI / test harness
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 2A: retrieve a broad Autobazar.eu candidate set for one car."
    )
    parser.add_argument("--brand", default="Škoda")
    parser.add_argument("--model", default="Kodiaq")
    parser.add_argument("--variant", default="2.0 TDI")
    parser.add_argument("--year", type=int, default=2021)
    parser.add_argument("--fuel", default="Diesel")
    parser.add_argument("--km", type=int, default=121000)
    parser.add_argument("--price", type=int, default=22000)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--csv", default="autobazar_phase2a_candidates.csv")
    args = parser.parse_args(argv)

    spec = CarSpec(
        brand=args.brand, model=args.model, variant=args.variant,
        year=args.year, fuel=args.fuel, km=args.km, price=args.price,
    )

    print("Inventory car:")
    print(f"  {spec.brand} {spec.model} {spec.variant} | {spec.year} | "
          f"{spec.fuel} | {spec.km} km | €{spec.price}\n")

    try:
        outcome = search_candidates(spec, max_pages=args.max_pages, delay=args.delay)
    except BlockedError as exc:
        print("\n*** STOPPED: possible block / anti-bot challenge ***", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2

    df = to_dataframe_2a(outcome.rows)
    df.to_csv(args.csv, index=False)

    # ---- 1. search query / URLs -----------------------------------------
    print(f"1) Search query generated : {outcome.query!r}")
    print(f"   Page-1 URL             : {outcome.page_urls[0] if outcome.page_urls else '-'}")
    print(f"   Site reports total     : {outcome.total_reported} listings "
          f"(site maxPage={outcome.max_page_site})")

    # ---- 2. pages requested ---------------------------------------------
    print(f"\n2) Pages requested        : {outcome.pages_requested} "
          f"(cap={args.max_pages}, delay={args.delay}s)")
    for u in outcome.page_urls:
        print(f"     - {u}")

    # ---- 3. listings retrieved ------------------------------------------
    print(f"\n3) Listings retrieved     : {len(df)} (deduplicated by URL)")

    # ---- 4. table --------------------------------------------------------
    print("\n4) Candidate table:")
    import pandas as pd
    with pd.option_context("display.max_columns", None, "display.width", 220):
        preview = df.copy()
        preview["title"] = preview["title"].str.slice(0, 34)
        preview["url"] = preview["url"].str.replace(BASE, "", regex=False).str.slice(0, 30)
        print(preview.to_string(index=True))

    # ---- 5. clearly-irrelevant rows (report only, NOT filtered out) ------
    print("\n5) Clearly-irrelevant-looking rows (reported, NOT removed):")
    want_fuel = (spec.fuel or "").strip().lower()
    want_model = (spec.model or "").strip().lower()

    def _s(value) -> str:
        """Safe string: treat None / NaN / non-str as empty (never .lower() a NaN)."""
        if value is None:
            return ""
        if isinstance(value, float):  # pandas NaN comes through as float
            return ""
        return str(value)

    flagged = []
    for i, r in df.iterrows():
        reasons = []
        fuel = _s(r["fuel"]).lower()
        title = _s(r["title"]).lower()
        if want_fuel and fuel and fuel != want_fuel:
            reasons.append(f"fuel={r['fuel']} (want {spec.fuel})")
        if want_model and title and want_model not in title:
            reasons.append("model name not in title")
        if reasons:
            flagged.append((i, r["title"], "; ".join(reasons)))
    if flagged:
        for i, title, why in flagged:
            print(f"   [row {i}] {str(title)[:40]:40} -> {why}")
    else:
        print("   none obvious (all rows share model + fuel with the inventory car)")

    # ---- 6. field availability ------------------------------------------
    print("\n   Field availability (non-null / total):")
    for col in OUTPUT_COLUMNS_2A:
        print(f"     {col:14}: {df[col].notna().sum()}/{len(df)}")

    print(f"\nSaved CSV: {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
