"""
Phase 7 - Full inventory run + transparent reporting.

Runs the whole ~98-car inventory through the Phase 6 pipeline (category-path
Autobazar retrieval + Bazos keyword, three-tier adaptive matching, robust price
stats) and produces the five deliverables requested:

  1. master_summary.csv        - one row per inventory car (both sources side by side)
  2. comparables_car{ri}_{src}_{tier}.csv - the underlying comparable listings + URLs
  3. ranked_report.csv         - most -> least below market (by primary-source pct)
  4. insufficient_report.csv   - cars with insufficient data OR failed retrieval
  5. diagnostics (printed + diagnostics.txt) - HIGH/MED/LOW/INSUFFICIENT counts,
                                 source coverage, disagreement, worst models

Design constraints honoured:
  * Autobazar and Bazos are kept COMPLETELY separate. Their medians are never
    averaged. Both are reported independently.
  * Confidence is a TRANSPARENT FLAG (HIGH/MEDIUM/LOW/INSUFFICIENT) with written
    reasons - never a single opaque 0-100 score.
  * Confidence NEVER alters the underlying price-difference calculation. Ranking
    is driven purely by price_difference_pct.

Efficiency / resilience:
  * Market retrieval is cached per (brand, model) - it does not depend on the
    individual car - which roughly halves live requests.
  * The master summary is checkpointed after every car so a timeout can resume
    (already-processed row_indexes are skipped on restart).

No anti-bot circumvention: retrieval reuses the Phase 6 fetchers, which stop and
report on any block/challenge.
"""
from __future__ import annotations

import argparse
import os
from collections import Counter, defaultdict
from typing import Optional

import pandas as pd

from phase6_validate import (
    InvCar, load_inventory, to_invcar,
    retrieve_autobazar, retrieve_bazos,
    match, adaptive_estimate, retained_pool,
    MIN_USABLE, BROAD, fmt, top_links,
    MILEAGE_RANK,
)

# Cross-source spread thresholds (percentage points of median spread).
AGREE_MAX = 15.0        # <= reasonable agreement
DISAGREE_MAX = 30.0     # (AGREE_MAX, DISAGREE_MAX] = meaningful; > = large
# A comparable pool is "data-complete enough" if <= this fraction miss year/km.
UNKNOWN_FRAC_MAX = 0.40

CRITICAL_FIELDS = ("year", "km", "price", "fuel")


# --------------------------------------------------------------------------- #
# Transparent confidence flag
# --------------------------------------------------------------------------- #
def missing_critical(car: InvCar) -> list[str]:
    miss = []
    if car.year is None:
        miss.append("year")
    if car.km is None:
        miss.append("km")
    if car.price is None:
        miss.append("price")
    if not car.fuel:
        miss.append("fuel")
    return miss


def median_spread_pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a and b:
        hi, lo = max(a, b), min(a, b)
        return (hi - lo) / hi * 100.0
    return None


def _mileage_headline(usable: list[dict]) -> dict:
    """
    Pick the mileage evidence that drives the confidence cap: the usable source
    with the MOST comparables (Autobazar wins ties elsewhere; here we just take
    max count). Sources are never merged - this only SELECTS one source's
    already-computed mileage assessment to headline in the reasons.
    """
    return max(usable, key=lambda e: e["comparable_count"]).get("mileage", {}) or {}


# Tier reached -> baseline confidence. Ordered loosest -> tightest so
# `max()` over ranks picks the tightest tier reached across both sources.
_TIER_RANK = {"broad": 1, "moderate": 2, "strict": 3}
_BASELINE_BY_RANK = {1: "LOW", 2: "MEDIUM", 3: "HIGH"}


def confidence_flag(car: InvCar, ab: dict, bz: dict) -> tuple[str, str]:
    """
    Return (FLAG, human-readable reasons). It does NOT feed back into any
    price/median calculation.

    2026-08-25: confidence is primarily TIER-driven, not sample-count-driven.
    INSUFFICIENT is reserved for literally zero comparables anywhere (neither
    source matched at any tolerance tier) -- any source with >=1 match, at
    any tier, gives a real baseline instead:
        best tier reached (either source) == strict   -> HIGH
        best tier reached (either source) == moderate -> MEDIUM
        best tier reached (either source) == broad     -> LOW
    "best tier reached" takes the tightest tier across BOTH sources, so one
    source alone reaching STRICT is enough for a HIGH baseline even if the
    other source is empty or only reaches BROAD.

    Data completeness, cross-source agreement, and MILEAGE SIMILARITY (how
    well the comparable-mileage distribution matches the submitted car)
    still cap that baseline DOWNWARD, exactly the role they always played --
    they decide whether the tier-earned baseline is honored or capped, never
    whether a level is reachable in the first place, and never raise it:
      * A LARGE / VERY_LARGE mileage gap, large source disagreement, or
        incomplete inventory/comparable data is a serious quality problem ->
        hard-capped at LOW (see phase6_validate.mileage_similarity for the
        mileage thresholds).
      * MODERATE or UNKNOWN mileage soft-caps a HIGH baseline down to MEDIUM
        (never all the way to LOW on its own).
    """
    ab_n, bz_n = ab["comparable_count"], bz["comparable_count"]
    ab_has, bz_has = ab_n > 0, bz_n > 0
    usable = [e for e, has in ((ab, ab_has), (bz, bz_has)) if has]

    if not usable:
        return "INSUFFICIENT", "no comparables found for this car at any tolerance tier"

    best_rank = max(_TIER_RANK[e["tier_used"]] for e in usable)
    baseline = _BASELINE_BY_RANK[best_rank]
    best_tier_name = {v: k for k, v in _TIER_RANK.items()}[best_rank]

    miss = missing_critical(car)
    inv_complete = not miss
    best_unknown = min(e["unknown_year_km_frac"] for e in usable)
    comp_complete = best_unknown <= UNKNOWN_FRAC_MAX

    both_usable = ab_has and bz_has
    spread = median_spread_pct(ab["market_median"], bz["market_median"])
    if both_usable and spread is not None:
        agreement = ("agree" if spread <= AGREE_MAX
                     else "meaningful" if spread <= DISAGREE_MAX
                     else "large")
    else:
        agreement = "single"  # only one usable source: cannot corroborate

    # Mileage similarity across the usable sources (transparent, per-source).
    worst_mile_rank = max(MILEAGE_RANK.get(e.get("mileage_match", "unknown"), 1.5) for e in usable)
    headline_mile = _mileage_headline(usable)
    headline_cat = headline_mile.get("category", "unknown")
    headline_note = headline_mile.get("note", "")

    best_n = max(ab_n, bz_n)
    tier_note = f"tier reached: {best_tier_name} (best sample n={best_n})"

    # Hard cap at LOW: any serious quality problem, regardless of baseline.
    low_hits = []
    if agreement == "large":
        low_hits.append(f"large source disagreement {spread:.0f}%")
    if not comp_complete:
        low_hits.append(f"{best_unknown*100:.0f}% of comparables missing year/km")
    if not inv_complete:
        low_hits.append("inventory missing " + ",".join(miss))
    if worst_mile_rank >= MILEAGE_RANK["large"]:
        low_hits.append(headline_note or "comparable mileage substantially different from the submitted car")
    if low_hits:
        low_hits.append(tier_note)
        return "LOW", "; ".join(low_hits)

    # Soft cap at MEDIUM: mileage not a tight match (moderate gap or
    # unverifiable) pulls a HIGH baseline down one level. Never touches an
    # already-MEDIUM-or-LOW baseline (nothing left to cap it to).
    if baseline == "HIGH" and headline_cat in ("moderate", "unknown"):
        note = headline_note or (
            "comparable mileage somewhat different from the submitted car"
            if headline_cat == "moderate" else "comparable mileage could not be verified"
        )
        return "MEDIUM", "; ".join([note, tier_note])

    # No cap triggered -- report the tier-earned baseline, with corroboration
    # and mileage context for transparency.
    reasons = [tier_note]
    if both_usable and agreement == "agree":
        reasons.append(f"both sources usable (AB {ab_n}/BZ {bz_n}); median spread {spread:.0f}% <= {AGREE_MAX:.0f}%")
    elif agreement == "single":
        reasons.append(f"single usable source (AB {ab_n}/BZ {bz_n}); no cross-check")
    elif agreement == "meaningful":
        reasons.append(f"moderate source disagreement {spread:.0f}%")
    if headline_cat == "good":
        reasons.append("mileage closely matches comparable listings")
    return baseline, "; ".join(reasons)


# --------------------------------------------------------------------------- #
# Per-car processing
# --------------------------------------------------------------------------- #
def evaluate_car(car: InvCar, ab_df: pd.DataFrame, bz_df: pd.DataFrame,
                 ab_err: str, bz_err: str) -> tuple[dict, dict, dict, pd.DataFrame, pd.DataFrame]:
    """
    Pure comparison core (no disk I/O): run both sources through the adaptive
    estimator, build the transparent flat summary row, and return it together
    with the per-source estimates and the chosen-tier matched comparables.

    This is the SINGLE source of comparison truth. Both the full-inventory path
    (`process_car`) and the single-car path (`single_car.compare_single_car`)
    call it, so they cannot diverge.
    """
    ab_est, ab_matched = adaptive_estimate(car, ab_df)
    bz_est, bz_matched = adaptive_estimate(car, bz_df)

    # Parallel, additive view: the disjoint STRICT/MODERATE-only/BROAD-only
    # retained pool (see retained_pool's docstring). Not yet used for pricing
    # or confidence -- adaptive_estimate above remains the sole production
    # estimator. This only surfaces per-tier counts for inspection/UI; the
    # full tagged DataFrames are available by calling retained_pool(car, df)
    # directly (same inputs any caller here already has).
    ab_retained = retained_pool(car, ab_df)
    bz_retained = retained_pool(car, bz_df)

    flag, reasons = confidence_flag(car, ab_est, bz_est)
    spread = median_spread_pct(ab_est["market_median"], bz_est["market_median"])
    pct_gap = None
    if ab_est["undervaluation_pct"] is not None and bz_est["undervaluation_pct"] is not None:
        pct_gap = round(ab_est["undervaluation_pct"] - bz_est["undervaluation_pct"], 1)

    # Primary source for ranking = the one with more comparables (Autobazar wins
    # ties, being the higher-quality structured source). We NEVER average.
    if ab_est["comparable_count"] >= bz_est["comparable_count"]:
        rank_source, rank_est = "autobazar", ab_est
    else:
        rank_source, rank_est = "bazos", bz_est
    rank_pct = rank_est["undervaluation_pct"]

    row = {
        "row_index": car.row_index,
        "brand": car.brand,
        "model": car.model,
        "variant": car.variant_raw,
        "fuel": car.fuel,
        "year": car.year,
        "km": car.km,
        "power_kw": car.power_kw,
        "inventory_price_eur": car.price,

        # --- Autobazar (independent) ---
        "ab_tier": ab_est["tier_used"],
        "ab_comparable_count": ab_est["comparable_count"],
        "ab_median_eur": ab_est["market_median"],
        "ab_price_diff_pct": ab_est["undervaluation_pct"],
        "ab_insufficient": ab_est["insufficient_sample"],
        "ab_unknown_frac": ab_est["unknown_year_km_frac"],
        "ab_outliers_trimmed": ab_est["outliers_trimmed"],
        "ab_mileage_match": ab_est["mileage_match"],
        "ab_comp_km_median": ab_est["mileage"]["comp_km_median"],
        "ab_comp_km_p25": ab_est["mileage"]["comp_km_p25"],
        "ab_comp_km_p75": ab_est["mileage"]["comp_km_p75"],
        "ab_mileage_note": ab_est["mileage"]["note"],

        # --- Autobazar retained pool (disjoint STRICT/MODERATE-only/BROAD-only;
        # observational only, not yet used for pricing -- see retained_pool()) ---
        "ab_n_strict": ab_retained["n_strict"],
        "ab_n_moderate": ab_retained["n_moderate"],
        "ab_n_broad": ab_retained["n_broad"],
        "ab_n_retained": ab_retained["n_total"],

        # --- Bazos (independent) ---
        "bz_tier": bz_est["tier_used"],
        "bz_comparable_count": bz_est["comparable_count"],
        "bz_median_eur": bz_est["market_median"],
        "bz_price_diff_pct": bz_est["undervaluation_pct"],
        "bz_insufficient": bz_est["insufficient_sample"],
        "bz_unknown_frac": bz_est["unknown_year_km_frac"],
        "bz_outliers_trimmed": bz_est["outliers_trimmed"],
        "bz_mileage_match": bz_est["mileage_match"],
        "bz_comp_km_median": bz_est["mileage"]["comp_km_median"],
        "bz_comp_km_p25": bz_est["mileage"]["comp_km_p25"],
        "bz_comp_km_p75": bz_est["mileage"]["comp_km_p75"],
        "bz_mileage_note": bz_est["mileage"]["note"],

        # --- Bazos retained pool (disjoint STRICT/MODERATE-only/BROAD-only;
        # observational only, not yet used for pricing -- see retained_pool()) ---
        "bz_n_strict": bz_retained["n_strict"],
        "bz_n_moderate": bz_retained["n_moderate"],
        "bz_n_broad": bz_retained["n_broad"],
        "bz_n_retained": bz_retained["n_total"],

        # --- cross-source (shown, never merged into one price) ---
        "median_spread_pct": round(spread, 1) if spread is not None else None,
        "ab_vs_bz_pct_gap": pct_gap,

        # --- transparency ---
        "missing_critical_fields": ",".join(missing_critical(car)) or "none",
        "sample_sufficient": (not ab_est["insufficient_sample"]) or (not bz_est["insufficient_sample"]),
        "confidence_flag": flag,
        "confidence_reasons": reasons,

        # --- ranking driver (kept explicit, not an average) ---
        "rank_source": rank_source,
        "rank_price_diff_pct": rank_pct,

        # --- audit links ---
        "ab_example_links": " | ".join(top_links(ab_matched)),
        "bz_example_links": " | ".join(top_links(bz_matched)),
        "ab_retrieval_error": ab_err or "",
        "bz_retrieval_error": bz_err or "",
    }
    return row, ab_est, bz_est, ab_matched, bz_matched


def process_car(car: InvCar, ab_df: pd.DataFrame, bz_df: pd.DataFrame,
                ab_err: str, bz_err: str, out_dir: str) -> dict:
    """
    Full-run wrapper around `evaluate_car`: runs the comparison and persists the
    chosen-tier comparables (with URLs) to `out_dir` for later inspection.
    Behaviour is identical to the original single-function implementation.
    """
    row, ab_est, bz_est, ab_matched, bz_matched = evaluate_car(
        car, ab_df, bz_df, ab_err, bz_err
    )
    for src, est, matched in (("autobazar", ab_est, ab_matched),
                              ("bazos", bz_est, bz_matched)):
        if not matched.empty:
            matched.to_csv(
                f"{out_dir}/comparables_car{car.row_index}_{src}_{est['tier_used']}.csv",
                index=False,
            )
    return row


# --------------------------------------------------------------------------- #
# Full run
# --------------------------------------------------------------------------- #
def run(inv_path: str, max_pages: int, delay: float, out_dir: str,
        rows: Optional[set[int]] = None) -> None:
    os.makedirs(out_dir, exist_ok=True)
    checkpoint = f"{out_dir}/master_summary.csv"

    inv = load_inventory(inv_path)
    cars = [to_invcar(r) for _, r in inv.iterrows()]
    if rows:
        cars = [c for c in cars if c.row_index in rows]
        print(f"Inventory loaded; restricting to {len(cars)} target row(s): "
              f"{sorted(rows)}")
    else:
        print(f"Inventory loaded: {len(cars)} cars.")

    # Resume support.
    done: dict[int, dict] = {}
    if os.path.exists(checkpoint):
        prev = pd.read_csv(checkpoint)
        for _, r in prev.iterrows():
            done[int(r["row_index"])] = r.to_dict()
        print(f"Resuming: {len(done)} cars already processed, skipping them.")

    # Market-retrieval cache keyed on (brand, model) - independent of the car.
    market_cache: dict[tuple, tuple] = {}

    def get_market(car: InvCar):
        key = (car.brand.strip().lower(), car.model.strip().lower())
        if key not in market_cache:
            ab_df, ab_err = retrieve_autobazar(car, max_pages, delay)
            bz_df, bz_err = retrieve_bazos(car, max_pages, delay)
            market_cache[key] = (ab_df, ab_err or "", bz_df, bz_err or "")
            print(f"    retrieved {key[0]}/{key[1]}: AB={len(ab_df)} BZ={len(bz_df)}"
                  + (f" [AB:{ab_err}]" if ab_err else "")
                  + (f" [BZ:{bz_err}]" if bz_err else ""))
        return market_cache[key]

    rows: list[dict] = list(done.values())
    for i, car in enumerate(cars, 1):
        if car.row_index in done:
            continue
        print(f"[{i}/{len(cars)}] #{car.row_index} {car.brand} {car.model} "
              f"{car.fuel} {car.year} EUR {car.price}")
        ab_df, ab_err, bz_df, bz_err = get_market(car)
        row = process_car(car, ab_df, bz_df, ab_err, bz_err, out_dir)
        print(f"    -> AB {row['ab_comparable_count']}@{row['ab_tier']} "
              f"{row['ab_price_diff_pct']}% | BZ {row['bz_comparable_count']}@{row['bz_tier']} "
              f"{row['bz_price_diff_pct']}% | {row['confidence_flag']}")
        rows.append(row)
        # Checkpoint after every car.
        pd.DataFrame(rows).to_csv(checkpoint, index=False)

    master = pd.DataFrame(rows).sort_values("row_index").reset_index(drop=True)
    master.to_csv(checkpoint, index=False)
    build_reports(master, out_dir)


# --------------------------------------------------------------------------- #
# Reports 3-5
# --------------------------------------------------------------------------- #
def build_reports(master: pd.DataFrame, out_dir: str) -> None:
    # (3) Ranked report: cars with a usable ranking pct, most -> least below market.
    rankable = master[master["rank_price_diff_pct"].notna()].copy()
    rankable = rankable.sort_values("rank_price_diff_pct", ascending=False)
    ranked_cols = [
        "row_index", "brand", "model", "variant", "fuel", "year", "km", "power_kw",
        "inventory_price_eur",
        "ab_comparable_count", "ab_median_eur", "ab_price_diff_pct",
        "bz_comparable_count", "bz_median_eur", "bz_price_diff_pct",
        "ab_vs_bz_pct_gap", "median_spread_pct",
        "rank_source", "rank_price_diff_pct",
        "confidence_flag", "sample_sufficient", "missing_critical_fields",
        "ab_tier", "bz_tier", "confidence_reasons",
        "ab_example_links", "bz_example_links",
    ]
    ranked = rankable[ranked_cols]
    ranked.to_csv(f"{out_dir}/ranked_report.csv", index=False)

    # (4) Insufficient / failed report.
    insuff = master[
        (master["confidence_flag"] == "INSUFFICIENT")
        | (master["rank_price_diff_pct"].isna())
        | (master["ab_retrieval_error"].astype(str).str.startswith("BLOCKED"))
        | (master["bz_retrieval_error"].astype(str).str.startswith("BLOCKED"))
    ].copy()
    insuff_cols = [
        "row_index", "brand", "model", "fuel", "year", "inventory_price_eur",
        "ab_comparable_count", "bz_comparable_count",
        "missing_critical_fields", "confidence_flag", "confidence_reasons",
        "ab_retrieval_error", "bz_retrieval_error",
    ]
    insuff[insuff_cols].to_csv(f"{out_dir}/insufficient_report.csv", index=False)

    # (5) Diagnostics.
    lines: list[str] = []

    def out(s=""):
        lines.append(s)
        print(s)

    n = len(master)
    flag_counts = master["confidence_flag"].value_counts().to_dict()
    ab_has = int((master["ab_comparable_count"] > 0).sum())
    bz_has = int((master["bz_comparable_count"] > 0).sum())
    both_has = int(((master["ab_comparable_count"] > 0) & (master["bz_comparable_count"] > 0)).sum())
    ab_usable = int((master["ab_comparable_count"] >= MIN_USABLE).sum())
    bz_usable = int((master["bz_comparable_count"] >= MIN_USABLE).sum())
    big_disagree = int((master["median_spread_pct"] > DISAGREE_MAX).sum())

    # models most frequently insufficient (no usable sample in either source)
    insuff_mask = ~master["sample_sufficient"].astype(bool)
    model_insuff = Counter(
        f"{b} {m}" for b, m in
        master.loc[insuff_mask, ["brand", "model"]].itertuples(index=False, name=None)
    )
    # retrieval failures per model
    fail_mask = (
        master["ab_retrieval_error"].astype(str).str.startswith("BLOCKED")
        | master["bz_retrieval_error"].astype(str).str.startswith("BLOCKED")
        | ((master["ab_comparable_count"] == 0) & (master["bz_comparable_count"] == 0))
    )
    model_fail = Counter(
        f"{b} {m}" for b, m in
        master.loc[fail_mask, ["brand", "model"]].itertuples(index=False, name=None)
    )

    out("\n" + "=" * 70)
    out("DIAGNOSTIC SUMMARY")
    out("=" * 70)
    out(f"Total inventory cars processed : {n}")
    out("")
    out("Confidence flags:")
    for f in ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT"):
        out(f"  {f:12}: {flag_counts.get(f, 0)}")
    out("")
    out("Source coverage (>=1 comparable):")
    out(f"  Autobazar data : {ab_has}/{n}")
    out(f"  Bazos data     : {bz_has}/{n}")
    out(f"  BOTH sources   : {both_has}/{n}")
    out("Source coverage (>=%d usable comparables):" % MIN_USABLE)
    out(f"  Autobazar usable: {ab_usable}/{n}")
    out(f"  Bazos usable    : {bz_usable}/{n}")
    out("")
    out(f"Significant cross-source disagreement (>{DISAGREE_MAX:.0f}% median spread): {big_disagree}")
    out("")
    out("Models most frequently producing INSUFFICIENT samples:")
    for name, c in model_insuff.most_common(12):
        out(f"  {c:2}x  {name}")
    if not model_insuff:
        out("  (none)")
    out("")
    out("Models with retrieval failure (no listings from either source):")
    for name, c in model_fail.most_common(12):
        out(f"  {c:2}x  {name}")
    if not model_fail:
        out("  (none)")

    # Ranked head/tail preview
    out("\n" + "=" * 70)
    out("RANKED - most below market (top 15)")
    out("=" * 70)
    preview_cols = ["row_index", "brand", "model", "year", "inventory_price_eur",
                    "ab_price_diff_pct", "bz_price_diff_pct", "rank_source",
                    "rank_price_diff_pct", "confidence_flag"]
    with pd.option_context("display.max_columns", None, "display.width", 220):
        out(ranked[preview_cols].head(15).to_string(index=False))

    with open(f"{out_dir}/diagnostics.txt", "w") as fh:
        fh.write("\n".join(lines))

    print("\nSaved:")
    for fn in ("master_summary.csv", "ranked_report.csv",
               "insufficient_report.csv", "diagnostics.txt"):
        print(f"  {out_dir}/{fn}")
    print(f"  {out_dir}/comparables_car*_*.csv  (per-car per-source comparables)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", default="inventory_sample.csv")
    ap.add_argument("--max-pages", type=int, default=3)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--out", default="phase7_out")
    ap.add_argument(
        "--rows",
        default=None,
        help="Restrict to specific inventory row_index values. Either a "
        "comma-separated list (e.g. '14,57,77') or a path to a CSV with a "
        "'row_index' column. Omit to run the full inventory.",
    )
    args = ap.parse_args()

    rows: Optional[set[int]] = None
    if args.rows:
        if os.path.exists(args.rows):
            rows = set(pd.read_csv(args.rows)["row_index"].astype(int).tolist())
        else:
            rows = {int(x) for x in args.rows.split(",") if x.strip()}

    run(args.inventory, args.max_pages, args.delay, args.out, rows)


if __name__ == "__main__":
    main()
