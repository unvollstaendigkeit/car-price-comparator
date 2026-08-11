"""
Autobazar retrieval (category-path + client-side filter).

Goal for this step
------------------
For ONE inventory vehicle, retrieve a BROAD but clean candidate set from the
category-restricted path:

    /vysledky/osobne-vozidla/{brand}/{model}/

...then filter locally for HIGH RECALL. We deliberately do NOT filter by year,
mileage, or exact variant yet.

Why the category path (verified against the live site)
------------------------------------------------------
The path is a genuine server-side filter, not a cosmetic URL. The embedded
`searchQuery` reports `brandSef=skoda`, `modelSef=kodiaq`, `category=30902`, and
every returned record is a Škoda Kodiaq. The site 308-redirects the generic
`osobne-vozidla` category into the correct body category (e.g.
`suv-terenne-vozidla`) while preserving the brand/model filter, so we simply
follow redirects instead of hard-coding a category slug.

This is materially cleaner than the keyword search, which spans the whole
marketplace and leaks spare-parts / unrelated-car listings on deeper pages.

Data preservation
------------------
Nothing is deleted. We emit two datasets:
  * raw_candidates      - every parsed listing, untouched
  * filtered_candidates - the valid passenger-car subset
Every raw row carries `kept` (bool) and `filter_reason` (str, "" when kept) so
the excluded rows can be inspected and the rules tuned later.

Anti-bot policy
---------------
Retrieval reuses `fetch()` from autobazar_scraper, which STOPS on any HTTP
401/403/429/5xx, `x-bot-request`, or CAPTCHA/Cloudflare marker. No bypass of any
kind is attempted.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field

try:
    import pandas as pd
except ImportError:  # required to build the tables / distributions
    pd = None

# Reuse the verified Phase 1 building blocks (URL construction, fetch with
# anti-bot detection, __NEXT_DATA__ parsing, normalisation, output schema).
from autobazar_scraper import (
    BASE,
    OUTPUT_COLUMNS,
    BlockedError,
    build_search_url,
    fetch,
    parse_listings,
)


# --------------------------------------------------------------------------- #
# Inventory vehicle spec
# --------------------------------------------------------------------------- #
@dataclass
class InventoryCar:
    brand: str
    model: str
    variant: str | None = None
    year: int | None = None
    fuel: str | None = None
    km: int | None = None
    price: int | None = None


# Keywords that betray a spare-part / accessory / non-passenger listing.
# The category path already restricts to cars, so this is a defensive net only.
# Matched as whole-word-ish substrings against the (lowercased) title.
_NON_CAR_KEYWORDS = (
    "náhradné diely", "nahradne diely", "náhradný diel", "use diely",
    "chladič", "chladic", "nárazník", "naraznik", "kapota", "blatník",
    "blatnik", "svetlomet", "svetlo", "zrkadlo", "spätné", "spatne",
    "prevodovka na predaj", "motor na predaj", "riadiaca jednotka",
    "airbag", "sada ", "pneumatik", "disky", "ráfik", "rafik",
    "palubná doska", "palubna doska", "sedadl", "výfuk", "vyfuk",
    "turbo na predaj", "vstrekovač", "vstrekovac",
)


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #
@dataclass
class RetrievalResult:
    car: InventoryCar
    base_url: str
    pages_requested: int
    raw_rows: list[dict]
    urls: list[str] = field(default_factory=list)
    parse_sources: list[str] = field(default_factory=list)


def retrieve_candidates(
    car: InventoryCar,
    max_pages: int = 5,
    delay: float = 1.5,
) -> RetrievalResult:
    """Fetch up to `max_pages` category-path result pages for one car.

    Broad by design: the ONLY server-side narrowing is brand + model (+ the
    site's own body category). All other filtering happens locally afterwards.
    """
    base_url = build_search_url(brand=car.brand, model=car.model, page=1)

    raw_rows: list[dict] = []
    urls: list[str] = []
    sources: list[str] = []
    seen_urls: set[str] = set()
    pages_done = 0

    for page in range(1, max_pages + 1):
        url = build_search_url(brand=car.brand, model=car.model, page=page)
        if delay and page > 1:
            time.sleep(delay)

        html = fetch(url)  # raises BlockedError -> caller stops & reports
        rows, source = parse_listings(html)
        urls.append(url)
        sources.append(source)
        pages_done += 1

        if not rows:  # no more results -> stop early
            break

        new_this_page = 0
        for r in rows:
            key = r.get("url") or f"{page}:{r.get('title')}"
            if key in seen_urls:
                continue
            seen_urls.add(key)
            r = dict(r)
            r["source_page"] = page
            raw_rows.append(r)
            new_this_page += 1

        # If a page adds nothing new, further pages are unlikely to help.
        if new_this_page == 0:
            break

    return RetrievalResult(
        car=car,
        base_url=base_url,
        pages_requested=pages_done,
        raw_rows=raw_rows,
        urls=urls,
        parse_sources=sources,
    )


# --------------------------------------------------------------------------- #
# Local filtering (high recall; keep everything, only annotate)
# --------------------------------------------------------------------------- #
def _looks_non_passenger(title: str | None) -> bool:
    if not title:
        return False
    t = title.lower()
    return any(kw in t for kw in _NON_CAR_KEYWORDS)


def annotate_filters(
    raw_rows: list[dict],
    car: InventoryCar,
    match_fuel: bool = True,
) -> list[dict]:
    """Return a copy of raw_rows with `kept` + `filter_reason` added.

    Exclusion rules (minimum set requested), each recorded as a reason:
      * year missing
      * km (mileage) missing
      * fuel missing
      * clearly not a passenger vehicle (part/accessory listing)
      * OPTIONAL: fuel does not match the inventory car's fuel (structured
        `fuel` field only; never inferred from the title). Enabled by default
        because a petrol car is not a valid comp for a diesel, but it is a
        distinct, separately-reported reason so it can be relaxed later.

    NOTE: we intentionally do NOT filter by year range, mileage range, or exact
    variant here — that is deferred to the (not-yet-built) scoring stage.
    """
    want_fuel = (car.fuel or "").strip().lower()
    annotated: list[dict] = []

    for r in raw_rows:
        reasons: list[str] = []

        if r.get("year") is None:
            reasons.append("year_missing")
        if r.get("km") is None:
            reasons.append("km_missing")

        fuel = r.get("fuel")
        if fuel is None:
            reasons.append("fuel_missing")
        elif match_fuel and want_fuel and str(fuel).strip().lower() != want_fuel:
            reasons.append(f"fuel_mismatch(got={fuel}, want={car.fuel})")

        if _looks_non_passenger(r.get("title")):
            reasons.append("not_passenger_vehicle")

        row = dict(r)
        row["kept"] = len(reasons) == 0
        row["filter_reason"] = "; ".join(reasons)
        annotated.append(row)

    return annotated


# --------------------------------------------------------------------------- #
# Distribution reporting (descriptive only; NOT used for scoring)
# --------------------------------------------------------------------------- #
def _mileage_bucket(km: int | None) -> str:
    if km is None:
        return "unknown"
    edges = [0, 50_000, 100_000, 150_000, 200_000, 250_000]
    labels = ["0-50k", "50-100k", "100-150k", "150-200k", "200-250k", "250k+"]
    for i in range(len(edges) - 1):
        if km < edges[i + 1]:
            return labels[i]
    return labels[-1]


def _power_bucket(kw: int | None) -> str:
    if kw is None:
        return "unknown"
    edges = [0, 90, 110, 130, 150, 190]
    labels = ["<90", "90-109", "110-129", "130-149", "150-189", "190+"]
    for i in range(len(edges) - 1):
        if kw < edges[i + 1]:
            return labels[i]
    return labels[-1]


def print_distributions(df) -> None:
    if df.empty:
        print("  (no filtered candidates to describe)")
        return

    print("\n  By year:")
    for year, n in df["year"].value_counts().sort_index().items():
        print(f"    {int(year)}: {n}")

    print("\n  By fuel:")
    for fuel, n in df["fuel"].value_counts().items():
        print(f"    {fuel}: {n}")

    print("\n  By power (kW bucket):")
    pb = df["power"].map(_power_bucket).value_counts()
    order = ["<90", "90-109", "110-129", "130-149", "150-189", "190+", "unknown"]
    for label in order:
        if label in pb:
            print(f"    {label}: {pb[label]}")

    print("\n  By mileage range:")
    mb = df["km"].map(_mileage_bucket).value_counts()
    order = ["0-50k", "50-100k", "100-150k", "150-200k",
             "200-250k", "250k+", "unknown"]
    for label in order:
        if label in mb:
            print(f"    {label}: {mb[label]}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    if pd is None:
        print("pandas is required: `uv pip install pandas`.", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(
        description="Retrieve Autobazar candidates for ONE car via category path."
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
    parser.add_argument("--raw-csv", default="autobazar_raw_candidates.csv")
    parser.add_argument("--filtered-csv", default="autobazar_filtered_candidates.csv")
    parser.add_argument(
        "--no-match-fuel", action="store_true",
        help="Do NOT drop fuel-mismatched listings (even higher recall).",
    )
    args = parser.parse_args(argv)

    car = InventoryCar(
        brand=args.brand, model=args.model, variant=args.variant,
        year=args.year, fuel=args.fuel, km=args.km, price=args.price,
    )

    try:
        result = retrieve_candidates(
            car, max_pages=args.max_pages, delay=args.delay
        )
    except BlockedError as exc:
        print("\n*** STOPPED: possible block / anti-bot challenge ***", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2

    annotated = annotate_filters(
        result.raw_rows, car, match_fuel=not args.no_match_fuel
    )

    raw_cols = OUTPUT_COLUMNS + ["source_page", "kept", "filter_reason"]
    raw_df = pd.DataFrame(annotated, columns=raw_cols)
    filtered_df = (
        raw_df[raw_df["kept"]]
        .drop(columns=["kept", "filter_reason"])
        .reset_index(drop=True)
    )

    raw_df.to_csv(args.raw_csv, index=False)
    filtered_df.to_csv(args.filtered_csv, index=False)

    # ---- report ----------------------------------------------------------
    print("=" * 72)
    print(f"Inventory car : {car.brand} {car.model} {car.variant or ''} "
          f"({car.year}, {car.fuel}, {car.km} km, €{car.price})")
    print(f"1. Category URL      : {result.base_url}")
    print(f"   Parse source(s)   : {sorted(set(result.parse_sources))}")
    print(f"2. Pages requested   : {result.pages_requested} (cap {args.max_pages}, "
          f"delay {args.delay}s)")
    print(f"3. Raw listings      : {len(raw_df)}")
    print(f"4. Valid candidates  : {len(filtered_df)}")

    excluded = raw_df[~raw_df["kept"]]
    print(f"5. Excluded listings : {len(excluded)}")
    if not excluded.empty:
        # Count by individual reason token (a row may have several).
        reason_counts: dict[str, int] = {}
        for reasons in excluded["filter_reason"]:
            for tok in reasons.split(";"):
                tok = tok.strip()
                # Collapse the parameterised fuel_mismatch(...) into one bucket.
                key = "fuel_mismatch" if tok.startswith("fuel_mismatch") else tok
                if key:
                    reason_counts[key] = reason_counts.get(key, 0) + 1
        for reason, n in sorted(reason_counts.items(), key=lambda x: -x[1]):
            print(f"     - {reason}: {n}")

    print(f"\n   raw_candidates      -> {args.raw_csv}")
    print(f"   filtered_candidates -> {args.filtered_csv}")

    # 6. Sample of filtered candidates.
    print("\n6. Sample filtered candidates (up to 20):")
    with pd.option_context("display.max_columns", None, "display.width", 220):
        sample = filtered_df.head(20).copy()
        if not sample.empty:
            sample["title"] = sample["title"].str.slice(0, 40)
        print(sample.to_string(index=True))

    # Extra: candidate-universe distributions (descriptive only).
    print("\n7. Filtered-candidate distributions (descriptive, NOT scoring):")
    print_distributions(filtered_df)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
