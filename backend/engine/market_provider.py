"""
Pluggable market-data transport for inventory analysis.

The inventory valuation pipeline (`analyze_inventory` -> `evaluate_car`) only
ever needs a normalized candidate pool per (source, brand, model). WHERE that
pool comes from — a live marketplace fetch or a saved snapshot — is orthogonal
to the valuation logic. This module isolates that transport so we can:

  * run the real pipeline against LIVE marketplace data (default, unchanged
    behavior), optionally RECORDING each normalized pool for later reuse, and
  * run the exact same pipeline against SAVED data with ZERO HTTP requests, to
    benchmark the valuation engine in isolation from scraping.

Nothing here changes the valuation methodology, matching rules, or confidence
calculation. It also adds no concurrency, proxies, or scraping workarounds — a
live fetch is still the same single-threaded, polite Phase-6 retrieval.

Key contract
------------
A provider exposes:

    retrieve(source, car, pages) -> RetrieveResult(df, err, elapsed_s, http_requests)

where `source` is "autobazar" | "bazos". The pool is keyed by
(source, brand, model, pages) because those are the ONLY inputs that affect the
live query (year/fuel/km/price are applied locally afterward by the matcher).
"""
from __future__ import annotations

import os
import pickle
import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from phase6_validate import retrieve_autobazar, retrieve_bazos

CACHE_MISS_ERR = "CACHE_MISS: no saved market pool for this (source, brand, model)"

# Default on-disk location for a saved store (used by the benchmark harness).
DEFAULT_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "fixtures", "market_cache.pkl"
)


def _norm(brand: str, model: str) -> tuple[str, str]:
    return (brand or "").strip().lower(), (model or "").strip().lower()


@dataclass
class RetrieveResult:
    df: pd.DataFrame
    err: str
    elapsed_s: float
    http_requests: int  # real network requests issued (0 for cached)


# --------------------------------------------------------------------------- #
# Store: normalized pools keyed by (source, brand, model, pages)
# --------------------------------------------------------------------------- #
@dataclass
class MarketStore:
    """
    A reusable snapshot of normalized marketplace pools. Values are the exact
    DataFrames returned by the Phase-6 retrievers (already normalized), so
    replaying them feeds `evaluate_car` byte-for-byte identical inputs.
    """

    entries: dict[tuple, dict] = field(default_factory=dict)

    # -- keys -------------------------------------------------------------- #
    @staticmethod
    def key(source: str, brand: str, model: str, pages: int) -> tuple:
        b, m = _norm(brand, model)
        return (source, b, m, int(pages))

    # -- write ------------------------------------------------------------- #
    def record(self, source: str, brand: str, model: str, pages: int,
               df: pd.DataFrame, err: str, elapsed_s: float) -> None:
        self.entries[self.key(source, brand, model, pages)] = {
            "df": df.copy(deep=True),
            "err": err or "",
            "elapsed_s": float(elapsed_s),
        }

    # -- read -------------------------------------------------------------- #
    def get(self, source: str, brand: str, model: str, pages: int) -> Optional[dict]:
        """
        Exact (source, brand, model, pages) hit, else the deepest snapshot with
        pages <= requested, else the deepest snapshot recorded for that model.
        Live retrieval only ever fetches at start_pages then (maybe) max_pages,
        and cache-only replays the same page requests, so the exact key hits in
        practice; the fallbacks just make the store forgiving.
        """
        exact = self.entries.get(self.key(source, brand, model, pages))
        if exact is not None:
            return exact
        b, m = _norm(brand, model)
        candidates = [
            (k[3], v) for k, v in self.entries.items()
            if k[0] == source and k[1] == b and k[2] == m
        ]
        if not candidates:
            return None
        at_or_below = [c for c in candidates if c[0] <= pages]
        pool = at_or_below or candidates
        pool.sort(key=lambda c: c[0])
        return pool[-1][1]

    def has_model(self, brand: str, model: str) -> bool:
        b, m = _norm(brand, model)
        return any(k[1] == b and k[2] == m for k in self.entries)

    def unique_models(self) -> list[tuple[str, str]]:
        seen: list[tuple[str, str]] = []
        for k in self.entries:
            pair = (k[1], k[2])
            if pair not in seen:
                seen.append(pair)
        return seen

    # -- persistence ------------------------------------------------------- #
    def save(self, path: str = DEFAULT_STORE_PATH) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(self.entries, fh, protocol=pickle.HIGHEST_PROTOCOL)
        return path

    @classmethod
    def load(cls, path: str = DEFAULT_STORE_PATH) -> "MarketStore":
        with open(path, "rb") as fh:
            return cls(entries=pickle.load(fh))

    @classmethod
    def load_or_empty(cls, path: str = DEFAULT_STORE_PATH) -> "MarketStore":
        try:
            return cls.load(path)
        except (FileNotFoundError, EOFError, pickle.UnpicklingError):
            return cls()


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
class LiveMarketProvider:
    """
    Real marketplace retrieval (the default, unchanged path). Optionally records
    every normalized pool into a MarketStore so a later cache-only run can reuse
    it. This is how you populate the cache from a live run.
    """

    is_live = True

    def __init__(self, delay: float = 1.0, store: Optional[MarketStore] = None):
        self.delay = delay
        self.store = store  # when set, live pools are saved for reuse

    def retrieve(self, source: str, car, pages: int) -> RetrieveResult:
        fn = retrieve_autobazar if source == "autobazar" else retrieve_bazos
        t0 = time.perf_counter()
        df, err = fn(car, pages, self.delay)
        elapsed = time.perf_counter() - t0
        err = err or ""
        if self.store is not None:
            self.store.record(source, car.brand, car.model, pages, df, err, elapsed)
        return RetrieveResult(df=df, err=err, elapsed_s=elapsed, http_requests=1)


class CachedMarketProvider:
    """
    Zero-HTTP replay from a MarketStore. NEVER calls Autobazar or Bazoš. On a
    miss it returns an empty pool with a CACHE_MISS marker (a non-blocking
    error) so the pipeline still runs end-to-end deterministically.
    """

    is_live = False

    def __init__(self, store: MarketStore):
        self.store = store

    def retrieve(self, source: str, car, pages: int) -> RetrieveResult:
        hit = self.store.get(source, car.brand, car.model, pages)
        if hit is None:
            return RetrieveResult(pd.DataFrame(), CACHE_MISS_ERR, 0.0, 0)
        # Copy so downstream mutations never poison the shared snapshot.
        return RetrieveResult(hit["df"].copy(deep=True), hit["err"], 0.0, 0)
