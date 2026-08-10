"""
Phase 6 - End-to-end validation on a real inventory sample (10 cars).

Pipeline per selected inventory car:
    normalized inventory row
        -> build market queries (brand + model)
        -> retrieve LIVE Autobazar.eu listings   (keyword search, few pages)
        -> retrieve LIVE Bazos.sk listings        (keyword search, few pages)
        -> normalize both into the common schema
        -> apply STRICT and BROAD comparability rules (per source, never merged)
        -> estimate market price + undervaluation + confidence

Reuses, without modification:
    * inventory_loader.load_inventory        (Phase 6 loader)
    * autobazar_scraper / bazos_scraper       (Phases 1-4 retrieval + parsing)
    * phase5_compare normalization + RuleSet  (Phase 5 rules & stats)

Deliberate deviations from Phase 5 (documented in the report):
    * brand/model are taken from the inventory car, NOT hardcoded to Škoda/Kodiaq.
    * MODEL is matched softly (locale renames: BMW "3 Series" -> SK "Rad 3").
      The BRAND is the hard identity guard against cross-brand contamination.

Anti-bot policy is unchanged: ordinary HTTP only; on any block we STOP that car
and record it, never circumvent. Request volume is kept low with delays.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from bs4 import BeautifulSoup

import autobazar_scraper as ab
import bazos_scraper as bz
from inventory_loader import load_inventory
from phase5_compare import (
    RuleSet, STRICT, BROAD,
    normalize_engine, normalize_fuel, normalize_transmission,
    _int, _clean, _listing_id_from_url, price_stats,
)

# Outcome tokens
MATCH, MISMATCH, UNKNOWN, NA = "match", "mismatch", "unknown", "n/a"

# Minimum plausible price (EUR) for a *running vehicle* listing. Keyword search
# returns spare-parts/accessory ads ("rozpredam na diely", bumpers, doors) at
# EUR 0-2 with no year/km/fuel; those would otherwise pass STRICT (unknown never
# fails) and destroy the median. This floor keeps even very cheap real cars
# (e.g. a 2006 Passat ~EUR 900) while removing parts.
VEHICLE_PRICE_FLOOR = 300

# Brand aliases so a listing titled "VW ..." still matches inventory "Volkswagen".
_BRAND_ALIASES = {
    "volkswagen": {"volkswagen", "vw"},
    "vw": {"volkswagen", "vw"},
    "mercedes": {"mercedes", "mercedes-benz", "benz"},
    "mercedes-benz": {"mercedes", "mercedes-benz", "benz"},
    "skoda": {"skoda", "škoda"},
    "škoda": {"skoda", "škoda"},
    "citroen": {"citroen", "citroën"},
}


# --------------------------------------------------------------------------- #
# Inventory car (superset of Phase 5 InventoryCar with extra display fields)
# --------------------------------------------------------------------------- #
@dataclass
class InvCar:
    row_index: int
    brand: str
    model: str
    variant_engine: Optional[str]
    fuel: Optional[str]
    year: Optional[int]
    km: Optional[int]
    price: Optional[int]
    power_kw: Optional[int]
    transmission: Optional[str]
    body_type: Optional[str]
    variant_raw: Optional[str]
    power_source: str

    def label(self) -> str:
        return (f"{self.brand} {self.model} {self.variant_raw or ''}".strip()
                + f" | {self.fuel} | {self.year} | "
                + (f"{self.km:,} km" if self.km else "? km")
                + f" | {self.power_kw or '?'}kW | EUR {self.price:,}"
                if self.price else "")


# --------------------------------------------------------------------------- #
# 1. Representative 10-car selection (deterministic, grounded in the real data)
# --------------------------------------------------------------------------- #
def select_ten(inv: pd.DataFrame) -> list[int]:
    """Pick 10 row_indexes spanning the real distribution + edge cases.

    Buckets are filled in priority order from the actual rows; each row is used
    once. This is reproducible and makes the coverage rationale explicit.
    """
    chosen: list[int] = []

    def take(mask, sort_by=None, ascending=True):
        sub = inv[mask & ~inv["row_index"].isin(chosen)]
        if sub.empty:
            return
        if sort_by:
            sub = sub.sort_values(sort_by, ascending=ascending)
        chosen.append(int(sub.iloc[0]["row_index"]))

    q = inv  # shorthand
    # 1. Common diesel SUV (best comparability on the SK market)
    take((q.fuel == "Diesel") & (q.body_type == "SUV"), "year", False)
    # 2. Common petrol SUV
    take((q.fuel == "Petrol") & (q.body_type == "SUV"), "km")
    # 3. Petrol estate (Stationcar)
    take((q.fuel == "Petrol") & (q.body_type == "Estate"))
    # 4. Petrol hatchback
    take((q.fuel == "Petrol") & (q.body_type == "Hatchback"))
    # 5. Hybrid with MISSING power (edge: null-power path)
    take((q.fuel == "Hybrid") & (q.power_kw.isna()))
    #    fallback: any hybrid
    take(q.fuel == "Hybrid")
    # 6. PHEV (rare fuel -> expect few comparables)
    take(q.fuel == "PHEV")
    # 7. Electric (very rare -> expect insufficient sample)
    take(q.fuel == "Electric")
    # 8. Premium German (locale model-name stress: BMW/Audi/Mercedes)
    take(q.brand.isin(["BMW", "Audi", "Mercedes"]))
    # 9. Oldest / highest-km car (extreme)
    take(pd.Series(True, index=q.index), "year", True)
    # 10. Newest car (extreme)
    take(pd.Series(True, index=q.index), "year", False)

    # Top up to 10 with the most common models if any bucket was empty.
    if len(chosen) < 10:
        for ri in inv.sort_values("year", ascending=False)["row_index"]:
            if int(ri) not in chosen:
                chosen.append(int(ri))
            if len(chosen) == 10:
                break
    return chosen[:10]


def to_invcar(row: pd.Series) -> InvCar:
    return InvCar(
        row_index=int(row["row_index"]),
        brand=str(row["brand"]),
        model=str(row["model"]),
        variant_engine=_clean(row.get("variant_engine")),
        fuel=_clean(row.get("fuel")),
        year=_int(row.get("year")),
        km=_int(row.get("km")),
        price=_int(row.get("price")),
        power_kw=_int(row.get("power_kw")),
        transmission=_clean(row.get("transmission")),
        body_type=_clean(row.get("body_type")),
        variant_raw=_clean(row.get("variant_raw")),
        power_source=str(row.get("power_source") or ""),
    )


# --------------------------------------------------------------------------- #
# 2. Live retrieval -> normalized common schema
# --------------------------------------------------------------------------- #
def _norm_market_row(source: str, raw: dict) -> dict:
    """Map a raw scraper dict into the common comparison schema."""
    if source == "autobazar":
        title = _clean(raw.get("title"))
        return {
            "source": "autobazar",
            "listing_id": _listing_id_from_url(raw.get("url")),
            "title": title,
            "variant_engine": normalize_engine(title),
            "fuel": normalize_fuel(raw.get("fuel")),
            "year": _int(raw.get("year")),
            "km": _int(raw.get("km")),
            "power_kw": _int(raw.get("power")),
            "transmission": normalize_transmission(raw.get("transmission")),
            "body_type": _clean(raw.get("body_type")),
            "price": _int(raw.get("price")),
            "url": _clean(raw.get("url")),
        }
    # bazos
    title = _clean(raw.get("title"))
    return {
        "source": "bazos",
        "listing_id": _clean(raw.get("listing_id")),
        "title": title,
        "variant_engine": normalize_engine(raw.get("engine")) or normalize_engine(title),
        "fuel": normalize_fuel(raw.get("fuel")),
        "year": _int(raw.get("year")),
        "km": _int(raw.get("km")),
        "power_kw": _int(raw.get("power")),
        "transmission": normalize_transmission(raw.get("transmission")),
        "body_type": _clean(raw.get("body_type")),
        "price": _int(raw.get("price")),
        "url": _clean(raw.get("url")),
    }


def retrieve_autobazar(car: InvCar, max_pages: int, delay: float) -> tuple[pd.DataFrame, Optional[str]]:
    keyword = f"{car.brand} {car.model}"
    rows: list[dict] = []
    try:
        for page in range(1, max_pages + 1):
            url = ab.build_search_url(keyword=keyword, page=page)
            html = ab.fetch(url)
            listings, _mode = ab.parse_listings(html)
            if not listings:
                break
            rows.extend(_norm_market_row("autobazar", r) for r in listings)
            if page < max_pages:
                time.sleep(delay)
    except ab.BlockedError as e:
        return pd.DataFrame(rows), f"BLOCKED: {e}"
    except Exception as e:  # noqa: BLE001 - surface, never hide
        return pd.DataFrame(rows), f"ERROR: {type(e).__name__}: {e}"
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset="url").reset_index(drop=True)
    return df, None


def retrieve_bazos(car: InvCar, max_pages: int, delay: float) -> tuple[pd.DataFrame, Optional[str]]:
    query = f"{car.brand} {car.model}"
    rows: list[dict] = []
    try:
        for page in range(1, max_pages + 1):
            url = bz.build_search_url(query, crp=bz.crp_for_page(page))
            html = bz.fetch(url)
            cards = bz.parse_page(html, query, page)
            if not cards:
                break
            rows.extend(_norm_market_row("bazos", r) for r in cards)
            if page < max_pages:
                time.sleep(delay)
    except bz.BlockedError as e:
        return pd.DataFrame(rows), f"BLOCKED: {e}"
    except Exception as e:  # noqa: BLE001
        return pd.DataFrame(rows), f"ERROR: {type(e).__name__}: {e}"
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset="listing_id").reset_index(drop=True)
    return df, None


# --------------------------------------------------------------------------- #
# 3. Comparability rules (locale-aware; brand hard, model soft)
# --------------------------------------------------------------------------- #
def _title_has_brand(car: InvCar, title: str) -> bool:
    aliases = _BRAND_ALIASES.get(car.brand.lower(), {car.brand.lower()})
    t = title.lower()
    return any(a in t for a in aliases)


def rule_brand(car: InvCar, row) -> str:
    title = _clean(row["title"])
    if not title:
        return UNKNOWN
    return MATCH if _title_has_brand(car, str(title)) else MISMATCH


def rule_model_soft(car: InvCar, row) -> str:
    title = _clean(row["title"])
    if not title:
        return UNKNOWN
    return MATCH if car.model.lower() in str(title).lower() else UNKNOWN


def rule_fuel(car: InvCar, row) -> str:
    if not car.fuel:
        return NA
    val = _clean(row["fuel"])
    if val is None:
        return UNKNOWN
    return MATCH if str(val).lower() == car.fuel.lower() else MISMATCH


def rule_variant(car: InvCar, row, require: bool) -> str:
    if not require or not car.variant_engine:
        return NA
    val = _clean(row["variant_engine"])
    if val is None:
        return UNKNOWN
    return MATCH if str(val).upper() == car.variant_engine.upper() else MISMATCH


def rule_year(car: InvCar, row, tol: int) -> str:
    if car.year is None:
        return NA
    val = _int(row["year"])
    if val is None:
        return UNKNOWN
    return MATCH if abs(val - car.year) <= tol else MISMATCH


def rule_km(car: InvCar, row, pct: float, floor: int) -> str:
    if car.km is None:
        return NA
    val = _int(row["km"])
    if val is None:
        return UNKNOWN
    window = max(int(car.km * pct), floor)
    return MATCH if abs(val - car.km) <= window else MISMATCH


def rule_power(car: InvCar, row, tol: Optional[int]) -> str:
    if tol is None or car.power_kw is None:
        return NA
    val = _int(row["power_kw"])
    if val is None:
        return UNKNOWN
    return MATCH if abs(val - car.power_kw) <= tol else MISMATCH


def rule_transmission(car: InvCar, row, require: bool) -> str:
    if not require or not car.transmission:
        return NA
    val = _clean(row["transmission"])
    if val is None:
        return UNKNOWN
    return MATCH if str(val).lower() == car.transmission.lower() else MISMATCH


def evaluate(car: InvCar, row, rules: RuleSet) -> tuple[bool, dict]:
    outcomes = {
        "brand": rule_brand(car, row),
        "model": rule_model_soft(car, row),
        "fuel": rule_fuel(car, row),
        "variant": rule_variant(car, row, rules.require_same_variant),
        "year": rule_year(car, row, rules.year_tol),
        "km": rule_km(car, row, rules.km_pct, rules.km_floor),
        "power": rule_power(car, row, rules.power_tol_kw),
        "transmission": rule_transmission(car, row, rules.require_same_transmission),
    }
    passed = not any(v == MISMATCH for v in outcomes.values())
    return passed, outcomes


def qualifies_as_vehicle(row) -> bool:
    """
    Candidate-qualification gate applied BEFORE tolerance rules.

    Keyword search returns spare-parts/accessory ads that carry the brand token
    in their title but are not cars (no year/km/fuel, EUR 0-2). Those otherwise
    pass STRICT because the tolerance rules treat missing fields as `unknown`
    (which never fails) - and they wreck the median. A real listing must:
      * have a plausible price (>= VEHICLE_PRICE_FLOOR), and
      * expose at least one vehicle attribute (year OR mileage).
    This keeps even very cheap genuine cars (e.g. a 2006 Passat ~EUR 900) while
    dropping parts. It is deliberately NOT a tolerance rule, so the
    "unknown never fails" principle is preserved for real vehicles.
    """
    price = _int(row.get("price"))
    if price is None or price < VEHICLE_PRICE_FLOOR:
        return False
    has_year = _int(row.get("year")) is not None
    has_km = _int(row.get("km")) is not None
    return has_year or has_km


def match(car: InvCar, df: pd.DataFrame, rules: RuleSet) -> tuple[pd.DataFrame, int]:
    """Return (matched_rows, n_disqualified_nonvehicles)."""
    if df.empty:
        return df.copy(), 0
    keep, trails, disq = [], [], 0
    for idx, row in df.iterrows():
        if not qualifies_as_vehicle(row):
            disq += 1
            continue
        ok, outcomes = evaluate(car, row, rules)
        if ok:
            keep.append(idx)
            trails.append(outcomes)
    out = df.loc[keep].copy().reset_index(drop=True)
    for f in ["brand", "model", "fuel", "variant", "year", "km", "power", "transmission"]:
        out[f"rule_{f}"] = [t[f] for t in trails]
    return out, disq


# --------------------------------------------------------------------------- #
# 4. Estimate + confidence
# --------------------------------------------------------------------------- #
def _unknown_fraction(df: pd.DataFrame) -> float:
    if df.empty:
        return 1.0
    unk = (df["year"].isna() | df["km"].isna()).sum()
    return unk / len(df)


def confidence_level(strict_n: int, unknown_frac: float) -> str:
    """Deterministic confidence from sample size and data completeness."""
    if strict_n == 0:
        base = "none"
    elif strict_n <= 3:
        base = "low"
    elif strict_n <= 7:
        base = "medium"
    else:
        base = "high"
    # downgrade one step if >40% of comparables miss year or km
    if unknown_frac > 0.40 and base in ("high", "medium", "low"):
        base = {"high": "medium", "medium": "low", "low": "low", "none": "none"}[base]
    return base


def estimate(car: InvCar, strict: pd.DataFrame) -> dict:
    stats = price_stats(strict) if not strict.empty else {"count": 0}
    n = stats.get("count", 0)
    unk = _unknown_fraction(strict)
    res = {
        "comparable_count": n,
        "market_median": stats.get("median"),
        "market_p25": stats.get("p25"),
        "market_p75": stats.get("p75"),
        "estimated_market_price": stats.get("median"),
        "confidence": confidence_level(n, unk),
        "insufficient_sample": n < 4,
        "unknown_year_km_frac": round(unk, 2),
    }
    if n and car.price:
        med = stats["median"]
        res["price_difference"] = med - car.price
        res["undervaluation_pct"] = round((med - car.price) / med * 100, 1) if med else None
    else:
        res["price_difference"] = None
        res["undervaluation_pct"] = None
    return res


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def fmt(v) -> str:
    return f"EUR {v:,.0f}" if isinstance(v, (int, float)) and v is not None else "n/a"


def top_links(df: pd.DataFrame, k: int = 3) -> list[str]:
    return [str(u) for u in df["url"].dropna().head(k).tolist()]


def run(inv_path: str, max_pages: int, delay: float, out_dir: str) -> None:
    import os
    os.makedirs(out_dir, exist_ok=True)

    inv = load_inventory(inv_path)
    idxs = select_ten(inv)
    picks = inv[inv["row_index"].isin(idxs)].set_index("row_index").loc[idxs].reset_index()

    print("############################################################")
    print("# PHASE 6 - End-to-end validation on 10 real inventory cars #")
    print("############################################################")
    print(f"\nInventory loaded: {len(inv)} cars. Selected 10 (spanning the real distribution):\n")
    for _, r in picks.iterrows():
        c = to_invcar(r)
        print(f"  #{c.row_index:>3} {c.brand} {c.model} | {c.fuel} | {c.body_type} | "
              f"{c.year} | {f'{c.km:,}km' if c.km else '?'} | "
              f"{c.power_kw or '?'}kW ({c.power_source}) | EUR {c.price:,}")
    print(f"\nSTRICT: {STRICT.describe()}")
    print(f"BROAD : {BROAD.describe()}")
    print("\nNOTE: prices assumed EUR on BOTH sides (inventory currency UNCONFIRMED).")
    print("Retrieval: keyword search, "
          f"{max_pages} page(s)/source, {delay}s delay. Sources never merged.\n")

    summary_rows = []
    for _, r in picks.iterrows():
        car = to_invcar(r)
        print("=" * 78)
        print(f"CAR #{car.row_index}: {car.brand} {car.model} ({car.fuel}, {car.year}) "
              f"- inventory price EUR {car.price:,}")

        ab_df, ab_err = retrieve_autobazar(car, max_pages, delay)
        time.sleep(delay)
        bz_df, bz_err = retrieve_bazos(car, max_pages, delay)

        print(f"  Autobazar: {len(ab_df)} listings"
              + (f"  [{ab_err}]" if ab_err else ""))
        print(f"  Bazos    : {len(bz_df)} listings"
              + (f"  [{bz_err}]" if bz_err else ""))

        groups, disq = {}, {}
        for name, src_df, ruleset in (
            ("autobazar_strict", ab_df, STRICT),
            ("autobazar_broad", ab_df, BROAD),
            ("bazos_strict", bz_df, STRICT),
            ("bazos_broad", bz_df, BROAD),
        ):
            groups[name], disq[name] = match(car, src_df, ruleset)
        for name, g in groups.items():
            if not g.empty:
                g.to_csv(f"{out_dir}/car{car.row_index}_{name}.csv", index=False)

        n_parts = disq["autobazar_broad"] + disq["bazos_broad"]
        if n_parts:
            print(f"  filtered {n_parts} non-vehicle/parts listing(s) before matching")

        # Combined strict pool (both sources, still reported separately below)
        ab_est = estimate(car, groups["autobazar_strict"])
        bz_est = estimate(car, groups["bazos_strict"])

        for src, est, gb in (("AUTOBAZAR", ab_est, groups["autobazar_broad"]),
                             ("BAZOS", bz_est, groups["bazos_broad"])):
            print(f"  -- {src} -- strict comparables={est['comparable_count']} "
                  f"(broad={len(gb)}) | "
                  f"median={fmt(est['market_median'])} | "
                  f"est_market={fmt(est['estimated_market_price'])} | "
                  f"diff={fmt(est['price_difference'])} | "
                  f"undervaluation={est['undervaluation_pct'] if est['undervaluation_pct'] is not None else 'n/a'}% | "
                  f"confidence={est['confidence']}"
                  + ("  [INSUFFICIENT SAMPLE]" if est['insufficient_sample'] else ""))

        # cross-source agreement note
        agree = ""
        if ab_est["market_median"] and bz_est["market_median"]:
            hi, lo = max(ab_est["market_median"], bz_est["market_median"]), min(ab_est["market_median"], bz_est["market_median"])
            spread = (hi - lo) / hi * 100
            agree = f"{spread:.0f}% median spread AB vs BZ"
            print(f"  cross-source: {agree}")

        for src, est in (("autobazar", ab_est), ("bazos", bz_est)):
            grp = groups[f"{src}_strict"]
            summary_rows.append({
                "row_index": car.row_index,
                "brand": car.brand, "model": car.model, "fuel": car.fuel,
                "year": car.year, "km": car.km, "power_kw": car.power_kw,
                "inventory_price_eur": car.price,
                "source": src,
                **est,
                "cross_source_note": agree,
                "example_links": " | ".join(top_links(grp)),
                "retrieval_error": (ab_err if src == "autobazar" else bz_err) or "",
            })

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(f"{out_dir}/phase6_summary.csv", index=False)
    print("\n" + "=" * 78)
    print(f"Saved per-car group CSVs and summary -> {out_dir}/phase6_summary.csv")
    print("\n=== SUMMARY (strict comparables) ===")
    show = summary[["row_index", "brand", "model", "fuel", "source",
                    "comparable_count", "estimated_market_price",
                    "undervaluation_pct", "confidence", "insufficient_sample"]]
    with pd.option_context("display.max_columns", None, "display.width", 220):
        print(show.to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", default="inventory_sample.csv")
    ap.add_argument("--max-pages", type=int, default=2)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--out", default="phase6_out")
    args = ap.parse_args()
    run(args.inventory, args.max_pages, args.delay, args.out)


if __name__ == "__main__":
    main()
