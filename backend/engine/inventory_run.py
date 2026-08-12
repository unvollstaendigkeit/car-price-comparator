"""
Inventory market-analysis orchestration.

This is a SCHEDULER/CACHE around the existing engine — it contains no new
valuation or comparison logic. Every car is valued by the exact same shared
core used by the single-car path (`build_canonical_car` + `evaluate_car`), so
inventory results and single-car results cannot diverge.

Efficiency model (the whole point of this layer):
  * The live marketplace query only ever depends on (brand, model) — never on
    year/fuel/km/price, which are applied LOCALLY afterward by the matcher.
    Therefore the retrieved candidate pool is cached per (brand, model) and
    reused for every car of that model at zero additional HTTP cost. Spec
    discrimination still happens per-car in `evaluate_car`.
  * Progressive depth: fetch 1 page/source first; only deepen to MAX_PAGES for a
    model when a car of that model is short of usable comparables AND the first
    page suggests more listings exist. Common models stay cheap; rare specs get
    the extra pages they need.

Resilience (no anti-bot circumvention):
  * Retrieval reuses the Phase-6 fetchers, which STOP and report on any block.
  * A global circuit breaker disables a source after BLOCK_LIMIT consecutive
    blocks; completed results are kept and the run continues on the other
    source. Nothing is retried aggressively; pacing is single-threaded with a
    polite delay between every request.

This module is transport-agnostic: `analyze_inventory` is a generator that
yields plain event dicts. `main.py` wraps them as SSE.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Iterator, Optional

import pandas as pd

import http_client
from phase6_validate import MIN_USABLE
from single_car import SingleCarInput, build_canonical_car
from phase7_fullrun import evaluate_car
from market_provider import LiveMarketProvider, RetrieveResult

# --- tunables (confirmed defaults) --------------------------------------- #
START_PAGES = 1          # pages/source fetched on first touch of a model
MAX_PAGES = 3            # hard cap; matches single-car retrieval depth
DELAY = 1.0              # seconds between requests (polite, single-threaded)
BLOCK_LIMIT = 3          # consecutive blocks on a source -> disable it
# Hard wall-clock ceiling for ALL retrieval of one (brand, model) group. Even
# though each request is individually bounded, a model can stack many sequential
# GETs (initial fetch + keyword fallback + progressive deepening + its own
# fallback). Without this ceiling one pathological model can burn minutes; with
# it, retrieval for a model stops the moment the budget is spent, keeps whatever
# was gathered, and the run moves on. This is a safety net, not the common path.
#
# CRITICAL: this MUST stay well below the serverless function's maxDuration (see
# vercel.json). The backend runs as a Vercel Python function that the platform
# HARD-KILLS at maxDuration seconds, taking the whole SSE stream (and any worker
# threads) with it. If one model's budget were >= maxDuration, that single slow
# model would burn the entire function and Vercel would terminate the request
# mid-stream BEFORE the model budget could ever fire — which looks exactly like
# "stuck at ~1m52s then dead". Keeping the per-model budget small guarantees the
# run keeps advancing through models within the function's lifetime.
MODEL_BUDGET_S = 25.0
# Only deepen a model's pool when the shallow fetch looks like it had a full
# page (i.e. more pages plausibly exist). Avoids wasting requests on models that
# genuinely only have a handful of ads.
DEEPEN_RAW_HINT = 10

_BLOCK_PREFIXES = ("BLOCKED", "ERROR")


def _is_block(err: str) -> bool:
    return bool(err) and err.startswith(_BLOCK_PREFIXES)


def _is_timeout(err: str) -> bool:
    """
    A per-request deadline overrun (SOFT failure). Distinct from a block: it must
    NOT trip the circuit breaker and it does not mean the source is unavailable —
    partial results may still have been kept. Timeout notes are emitted by the
    Phase-6 wrappers as 'TIMEOUT: ...' (possibly after a fallback note).
    """
    return "TIMEOUT" in (err or "")


def _is_budget_timeout(err: str) -> bool:
    """
    Specifically a per-MODEL time-budget overrun (the safety net), as opposed to
    an ordinary single-request timeout. Reported so the UI can say 'TIME BUDGET
    EXCEEDED' distinctly from a one-off slow page.
    """
    return "time budget" in (err or "").lower()


def _links(joined: str) -> list[str]:
    return [x for x in (joined or "").split(" | ") if x]


def _to_input(row: dict) -> Optional[SingleCarInput]:
    """Build a SingleCarInput from a confirmed inventory row (brand+model required)."""
    brand = (row.get("brand") or "").strip()
    model = (row.get("model") or "").strip()
    if not brand or not model:
        return None
    return SingleCarInput(
        brand=brand,
        model=model,
        variant=row.get("variant") or None,
        year=row.get("year"),
        fuel=row.get("fuel") or None,
        km=row.get("km"),
        price=row.get("price"),
        body_type=row.get("body_type") or None,
    )


def _compact(row: dict, row_index: int, row_number: int) -> dict:
    """Map the engine's flat summary row to the compact per-car analysis contract."""
    return {
        "row_index": row_index,
        "row_number": row_number,
        "brand": row["brand"],
        "model": row["model"],
        "variant": row["variant"],
        "year": row["year"],
        "km": row["km"],
        "fuel": row["fuel"],
        "power_kw": row["power_kw"],
        "asking_price_eur": row["inventory_price_eur"],
        "autobazar": {
            "median_eur": row["ab_median_eur"],
            "price_diff_pct": row["ab_price_diff_pct"],
            "comparable_count": row["ab_comparable_count"],
            "tier": row["ab_tier"],
            "insufficient": row["ab_insufficient"],
            "example_links": _links(row["ab_example_links"]),
            "error": row["ab_retrieval_error"] or None,
            "mileage_match": row["ab_mileage_match"],
            "comp_km_median": row["ab_comp_km_median"],
            "comp_km_p25": row["ab_comp_km_p25"],
            "comp_km_p75": row["ab_comp_km_p75"],
            "mileage_note": row["ab_mileage_note"],
        },
        "bazos": {
            "median_eur": row["bz_median_eur"],
            "price_diff_pct": row["bz_price_diff_pct"],
            "comparable_count": row["bz_comparable_count"],
            "tier": row["bz_tier"],
            "insufficient": row["bz_insufficient"],
            "example_links": _links(row["bz_example_links"]),
            "error": row["bz_retrieval_error"] or None,
            "mileage_match": row["bz_mileage_match"],
            "comp_km_median": row["bz_comp_km_median"],
            "comp_km_p25": row["bz_comp_km_p25"],
            "comp_km_p75": row["bz_comp_km_p75"],
            "mileage_note": row["bz_mileage_note"],
        },
        "median_spread_pct": row["median_spread_pct"],
        "ab_vs_bz_pct_gap": row["ab_vs_bz_pct_gap"],
        "rank_source": row["rank_source"],
        "rank_price_diff_pct": row["rank_price_diff_pct"],
        "confidence_flag": row["confidence_flag"],
        "confidence_reasons": row["confidence_reasons"],
        "missing_critical_fields": row["missing_critical_fields"],
    }


def analyze_inventory(
    rows: list[dict],
    *,
    delay: float = DELAY,
    start_pages: int = START_PAGES,
    max_pages: int = MAX_PAGES,
    block_limit: int = BLOCK_LIMIT,
    model_budget_s: float = MODEL_BUDGET_S,
    provider=None,
) -> Iterator[dict]:
    """
    Analyze a confirmed inventory. Yields event dicts:

      start | group_start | retrieved | source_timeout | group_timeout
            | source_disabled | car_done | summary

    Grouping by (brand, model) means marketplace lookups scale with the number
    of UNIQUE models, not the number of cars.

    `provider` is the market-data transport (see market_provider.py). It
    defaults to a LiveMarketProvider (real, polite retrieval) so existing
    callers are unchanged. Pass a CachedMarketProvider for a zero-HTTP run.
    The summary event carries benchmark timings (retrieval vs. valuation).

    `model_budget_s` is the hard wall-clock ceiling for ALL retrieval of one
    (brand, model) group. When exceeded, retrieval for that model stops
    immediately, whatever partial marketplace data was gathered is still
    evaluated, the model is marked TIME BUDGET EXCEEDED, and the run continues.
    This guarantees no single model can stall the whole inventory run.
    """
    if provider is None:
        provider = LiveMarketProvider(delay=delay)
    # Parse + group, preserving first-seen order for orderly progress.
    groups: "OrderedDict[tuple, list[tuple[int, int, SingleCarInput]]]" = OrderedDict()
    total_cars = 0
    for i, raw in enumerate(rows):
        inp = _to_input(raw)
        if inp is None:
            continue
        row_index = int(raw.get("row_index", i))
        row_number = int(raw.get("row_number", row_index + 1))
        key = (inp.brand.lower(), inp.model.lower())
        groups.setdefault(key, []).append((row_index, row_number, inp))
        total_cars += 1

    yield {
        "stage": "start",
        "total_cars": total_cars,
        "total_groups": len(groups),
    }

    # Fresh, run-scoped instrumentation window so http_client.request_count()
    # reflects only THIS run's real network GETs.
    http_client.reset_log()

    consec_blocks = {"autobazar": 0, "bazos": 0}
    disabled: set[str] = set()
    timeouts = {"autobazar": 0, "bazos": 0}   # per-source SOFT deadline overruns
    errors = {"autobazar": 0, "bazos": 0}     # per-source hard block/error notes
    model_timeouts = 0          # (brand, model) groups that hit the time budget
    market_lookups = 0          # provider.retrieve calls (both modes)
    http_requests = 0           # real network requests (0 when fully cached)
    cache_hits = 0              # provider.retrieve calls served from cache (0 HTTP)
    retrieval_time_s = 0.0      # wall time spent in provider.retrieve
    eval_time_s = 0.0           # wall time spent in evaluate_car (pure valuation)
    cached_groups = 0           # groups served with 0 HTTP (fully from cache)
    run_t0 = time.perf_counter()

    results: list[dict] = []
    flag_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "INSUFFICIENT": 0}
    analyzed = 0

    def peek(source: str, car, pages: int) -> Optional[dict]:
        """Ask the provider whether this source would be served from cache (no fetch)."""
        fn = getattr(provider, "peek", None)
        if fn is None:
            return None
        try:
            return fn(source, car, pages)
        except Exception:  # noqa: BLE001 - peek is advisory only
            return None

    # Small grace over the cooperative deadline so the lower layers get a chance
    # to stop cleanly (nicer notes, real partial data) before the watchdog fires.
    _BUDGET_GRACE_S = 2.0

    def _retrieve_within_budget(source: str, car, pages: int,
                                deadline: Optional[float]) -> RetrieveResult:
        """Run provider.retrieve, but never block past the per-model budget.

        Returns whatever the retrieve produced if it finishes in time; otherwise
        abandons the worker (which winds down on its own via the deadline it was
        given) and returns a budget-timeout result so the run moves on NOW."""
        if deadline is None:
            return provider.retrieve(source, car, pages, deadline=deadline)

        box: dict = {}
        done = threading.Event()

        def _work() -> None:
            try:
                box["res"] = provider.retrieve(source, car, pages, deadline=deadline)
            except BaseException as exc:  # noqa: BLE001 - carried to caller
                box["exc"] = exc
            finally:
                done.set()

        threading.Thread(
            target=_work, name=f"retrieve:{source}", daemon=True,
        ).start()

        budget_left = deadline - time.perf_counter()
        if not done.wait(max(0.0, budget_left) + _BUDGET_GRACE_S):
            # Overran even the grace window: abandon and move on immediately.
            return RetrieveResult(
                pd.DataFrame(),
                f"TIMEOUT: model time budget ({model_budget_s:.0f}s) exceeded; "
                f"abandoned {source} and continued", 0.0, 0,
            )
        if "exc" in box:
            raise box["exc"]
        return box["res"]

    def fetch(source: str, car, pages: int, deadline: Optional[float] = None) -> RetrieveResult:
        """One retrieval via the provider, honoring the circuit breaker and the
        per-model time budget (`deadline`, an absolute time.perf_counter())."""
        nonlocal market_lookups, http_requests, cache_hits, retrieval_time_s
        if source in disabled:
            return RetrieveResult(pd.DataFrame(), "DISABLED: source disabled after repeated blocks", 0.0, 0)
        # Budget already spent before we even start: skip the network entirely.
        if deadline is not None and time.perf_counter() >= deadline:
            return RetrieveResult(
                pd.DataFrame(),
                "TIMEOUT: model time budget exceeded before fetch", 0.0, 0,
            )

        # AUTHORITATIVE per-model hard stop. The lower layers cooperatively honor
        # `deadline` (page loops check it; each HTTP request is capped to the time
        # remaining), which normally keeps us inside the budget. This watchdog is
        # the guarantee that does NOT depend on every layer plumbing the deadline
        # perfectly: we run provider.retrieve on a worker and, if it overruns the
        # remaining budget, ABANDON it and move on. The abandoned worker winds
        # down on its own within one request cap (its next page-loop check sees
        # the deadline has passed), so background work stays bounded.
        res = _retrieve_within_budget(source, car, pages, deadline)
        err = res.err or ""
        market_lookups += 1
        http_requests += res.http_requests
        retrieval_time_s += res.elapsed_s
        if res.from_cache:
            cache_hits += 1
        if _is_block(err):
            consec_blocks[source] += 1
            errors[source] += 1
        else:
            consec_blocks[source] = 0
            # A timeout is a soft failure: count it, but it does NOT trip the
            # breaker and any partial rows in res.df are still used.
            if _is_timeout(err):
                timeouts[source] += 1
        return res

    def timed_evaluate(car, ab_df, bz_df, ab_err, bz_err):
        """Wrap evaluate_car to accumulate pure valuation time."""
        nonlocal eval_time_s
        t0 = time.perf_counter()
        out = evaluate_car(car, ab_df, bz_df, ab_err, bz_err)
        eval_time_s += time.perf_counter() - t0
        return out

    def _status(pk: Optional[dict]) -> dict:
        return {
            "from_cache": bool(pk and pk.get("from_cache")),
            "age_s": (pk or {}).get("age_s"),
        }

    for gi, (key, members) in enumerate(groups.items(), 1):
        # A representative car drives retrieval (query = brand+model only, so any
        # member of the group produces the identical marketplace query).
        rep_car = build_canonical_car(members[0][2])

        # Peek the cache (no fetch) so the UI can announce, per model, whether we
        # will reuse a cached pool (and how old it is) or retrieve live.
        cache_status = {
            "autobazar": _status(peek("autobazar", rep_car, start_pages)),
            "bazos": _status(peek("bazos", rep_car, start_pages)),
        }
        group_cached = cache_status["autobazar"]["from_cache"] and cache_status["bazos"]["from_cache"]

        yield {
            "stage": "group_start",
            "group_index": gi,
            "group_total": len(groups),
            "brand": rep_car.brand,
            "model": rep_car.model,
            "cars_in_group": len(members),
            "cached": group_cached,
            "cache_status": cache_status,
        }

        newly_disabled: list[str] = []

        # Hard per-model wall-clock budget: the initial fetches, the fallbacks
        # inside them, AND the progressive deepening below all share this single
        # deadline, so this (brand, model) group as a whole can never run past it
        # no matter how many sequential GETs it stacks.
        group_t0 = time.perf_counter()
        deadline = group_t0 + model_budget_s

        http_before = http_requests
        ab_res = fetch("autobazar", rep_car, start_pages, deadline)
        bz_res = fetch("bazos", rep_car, start_pages, deadline)
        if http_requests == http_before:
            cached_groups += 1  # served entirely from cache (0 HTTP)
        entry = {
            "ab_df": ab_res.df, "ab_err": ab_res.err, "ab_depth": start_pages,
            "bz_df": bz_res.df, "bz_err": bz_res.err, "bz_depth": start_pages,
        }
        for source in ("autobazar", "bazos"):
            if consec_blocks[source] >= block_limit and source not in disabled:
                disabled.add(source)
                newly_disabled.append(source)
        ab_timed_out = _is_timeout(ab_res.err)
        bz_timed_out = _is_timeout(bz_res.err)
        yield {
            "stage": "retrieved",
            "brand": rep_car.brand,
            "model": rep_car.model,
            "ab_count": int(len(ab_res.df)),
            "bz_count": int(len(bz_res.df)),
            "ab_error": ab_res.err or None,
            "bz_error": bz_res.err or None,
            "ab_from_cache": ab_res.from_cache,
            "bz_from_cache": bz_res.from_cache,
            "ab_age_s": ab_res.age_s,
            "bz_age_s": bz_res.age_s,
            "ab_timed_out": ab_timed_out,
            "bz_timed_out": bz_timed_out,
        }

        # A timeout keeps whatever partial rows we got and moves on. Announce it
        # per source so the UI can show e.g. "Bazoš — timed out, continuing".
        for source, timed_out, res in (
            ("autobazar", ab_timed_out, ab_res),
            ("bazos", bz_timed_out, bz_res),
        ):
            if timed_out:
                yield {
                    "stage": "source_timeout",
                    "source": source,
                    "brand": rep_car.brand,
                    "model": rep_car.model,
                    "kept": int(len(res.df)),
                    "reason": "request exceeded its time limit; kept partial results and continued",
                }

        # Whole-model budget overrun: distinct from a one-off slow page. Reported
        # once per model so the UI can show "TIME BUDGET EXCEEDED"; retrieval is
        # abandoned for this model (no deepening below) but partial data is kept
        # and every car in the group is still valued with whatever we have.
        group_budget_exceeded = _is_budget_timeout(ab_res.err) or _is_budget_timeout(bz_res.err)
        if group_budget_exceeded:
            model_timeouts += 1
            yield {
                "stage": "group_timeout",
                "brand": rep_car.brand,
                "model": rep_car.model,
                "budget_s": round(model_budget_s, 1),
                "elapsed_s": round(time.perf_counter() - group_t0, 2),
                "ab_kept": int(len(ab_res.df)),
                "bz_kept": int(len(bz_res.df)),
                "reason": (f"retrieval for this model exceeded its {model_budget_s:.0f}s "
                           f"time budget; kept partial results and moved on"),
            }

        for source in newly_disabled:
            yield {
                "stage": "source_disabled",
                "source": source,
                "reason": f"{block_limit} consecutive blocks; skipping this source for the rest of the run",
            }

        for (row_index, row_number, inp) in members:
            car = build_canonical_car(inp, row_index=row_index)
            engine_row, ab_est, bz_est, *_ = timed_evaluate(
                car, entry["ab_df"], entry["bz_df"], entry["ab_err"], entry["bz_err"]
            )

            # Progressive deepening: if this car is short on usable comparables
            # and the shallow pool hints at more pages, deepen the shared pool
            # (once) so this and later same-model cars benefit.
            need_deeper = (
                ab_est["comparable_count"] < MIN_USABLE
                or bz_est["comparable_count"] < MIN_USABLE
            )
            # Never deepen once the model's time budget is spent — that would
            # start fresh GETs that are guaranteed to overshoot. Also skip if the
            # budget has since run out mid-group.
            budget_left = time.perf_counter() < deadline
            if need_deeper and not group_budget_exceeded and budget_left:
                deepened = False
                if (
                    ab_est["comparable_count"] < MIN_USABLE
                    and entry["ab_depth"] < max_pages
                    and not _is_block(entry["ab_err"])
                    and "autobazar" not in disabled
                    and len(entry["ab_df"]) >= DEEPEN_RAW_HINT
                ):
                    ab_deep = fetch("autobazar", car, max_pages, deadline)
                    # Only adopt the deeper pool if it is a clean, richer result.
                    # A block or a budget/timeout that returned FEWER rows must not
                    # clobber the good shallow pool we already have.
                    if not _is_block(ab_deep.err) and not (
                        _is_timeout(ab_deep.err) and len(ab_deep.df) < len(entry["ab_df"])
                    ):
                        entry["ab_df"], entry["ab_err"], entry["ab_depth"] = ab_deep.df, ab_deep.err, max_pages
                        deepened = True
                if (
                    bz_est["comparable_count"] < MIN_USABLE
                    and entry["bz_depth"] < max_pages
                    and not _is_block(entry["bz_err"])
                    and "bazos" not in disabled
                    and len(entry["bz_df"]) >= DEEPEN_RAW_HINT
                    and time.perf_counter() < deadline
                ):
                    bz_deep = fetch("bazos", car, max_pages, deadline)
                    if not _is_block(bz_deep.err) and not (
                        _is_timeout(bz_deep.err) and len(bz_deep.df) < len(entry["bz_df"])
                    ):
                        entry["bz_df"], entry["bz_err"], entry["bz_depth"] = bz_deep.df, bz_deep.err, max_pages
                        deepened = True
                if deepened:
                    engine_row, ab_est, bz_est, *_ = timed_evaluate(
                        car, entry["ab_df"], entry["bz_df"], entry["ab_err"], entry["bz_err"]
                    )

            compact = _compact(engine_row, row_index, row_number)
            results.append(compact)
            flag_counts[compact["confidence_flag"]] = flag_counts.get(compact["confidence_flag"], 0) + 1
            analyzed += 1

            yield {
                "stage": "car_done",
                "analyzed": analyzed,
                "total": total_cars,
                "car": compact,
            }

    # Ranked most -> least below market (drop cars with no usable ranking pct).
    ranked = sorted(
        (r for r in results if r["rank_price_diff_pct"] is not None),
        key=lambda r: r["rank_price_diff_pct"],
        reverse=True,
    )

    total_time_s = time.perf_counter() - run_t0

    yield {
        "stage": "summary",
        "counts": {
            "total_cars": total_cars,
            "analyzed": analyzed,
            "high": flag_counts.get("HIGH", 0),
            "medium": flag_counts.get("MEDIUM", 0),
            "low": flag_counts.get("LOW", 0),
            "insufficient": flag_counts.get("INSUFFICIENT", 0),
        },
        "market_lookups": market_lookups,
        "disabled_sources": sorted(disabled),
        # Per-source soft timeouts / hard errors so the UI can report resilience.
        "timeouts": dict(timeouts),
        "errors": dict(errors),
        "timeouts_total": sum(timeouts.values()),
        "errors_total": sum(errors.values()),
        # Models that hit the whole-model time budget (the safety net firing).
        "model_timeouts": model_timeouts,
        "model_budget_s": round(model_budget_s, 1),
        # --- benchmark: how much runtime is retrieval vs. valuation? --------- #
        "benchmark": {
            "total_cars": total_cars,
            "unique_groups": len(groups),
            "cached_groups": cached_groups,
            "cache_hits": cache_hits,
            "http_requests": http_requests,
            # Cross-check against the transport's own monotonic counter; these
            # should match exactly (guards against accounting drift).
            "http_requests_measured": http_client.request_count(),
            "market_lookups": market_lookups,
            "retrieval_time_s": round(retrieval_time_s, 4),
            "eval_time_s": round(eval_time_s, 4),
            "total_time_s": round(total_time_s, 4),
            "avg_http_time_s": round(retrieval_time_s / http_requests, 4) if http_requests else 0.0,
            "timeouts_total": sum(timeouts.values()),
            "errors_total": sum(errors.values()),
            "model_timeouts": model_timeouts,
            "mode": "cache-only" if http_requests == 0 else "live",
        },
        "ranked": ranked,
    }
