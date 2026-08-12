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
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

import http_client
from phase6_validate import retrieve_autobazar, retrieve_bazos

CACHE_MISS_ERR = "CACHE_MISS: no saved market pool for this (source, brand, model)"

# Default on-disk location for a saved store (used by the benchmark harness and
# the live persistent cache backing Inventory mode).
DEFAULT_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "fixtures", "market_cache.pkl"
)

# How long a cached (source, brand, model) pool stays usable before it is
# considered expired and re-fetched live. 24h by default; configurable per run.
DEFAULT_TTL_S = 24 * 60 * 60


def _norm(brand: str, model: str) -> tuple[str, str]:
    return (brand or "").strip().lower(), (model or "").strip().lower()


@dataclass
class RetrieveResult:
    df: pd.DataFrame
    err: str
    elapsed_s: float
    http_requests: int  # real network requests issued (0 for cached)
    from_cache: bool = False        # served from a persisted/in-memory pool
    age_s: Optional[float] = None   # age of the cached pool in seconds (None if live/unknown)


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
               df: pd.DataFrame, err: str, elapsed_s: float,
               fetched_at: Optional[float] = None) -> None:
        self.entries[self.key(source, brand, model, pages)] = {
            "df": df.copy(deep=True),
            "err": err or "",
            "elapsed_s": float(elapsed_s),
            "pages": int(pages),
            "fetched_at": float(fetched_at if fetched_at is not None else time.time()),
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

    def get_fresh(self, source: str, brand: str, model: str, pages: int,
                  ttl_s: Optional[float]) -> Optional[dict]:
        """
        Return a NON-EXPIRED pool for (source, brand, model) whose depth is >=
        `pages`, or None. Depth matters because a shallow (1-page) snapshot must
        NOT satisfy a request that wants more pages — that would silently starve
        deepening. Among sufficiently-deep fresh entries, the shallowest is
        returned (closest match to what was asked for). ttl_s=None disables
        expiry. Entries without a fetched_at are treated as expired (safe: they
        get refreshed live).
        """
        b, m = _norm(brand, model)
        now = time.time()
        candidates: list[tuple[int, dict]] = []
        for k, v in self.entries.items():
            if k[0] != source or k[1] != b or k[2] != m:
                continue
            if k[3] < int(pages):
                continue
            fa = v.get("fetched_at")
            if fa is None:
                continue
            if ttl_s is not None and (now - fa) > ttl_s:
                continue
            candidates.append((k[3], v))
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[0])
        return candidates[0][1]

    @staticmethod
    def age_of(entry: dict) -> Optional[float]:
        fa = entry.get("fetched_at")
        return (time.time() - fa) if fa is not None else None

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
        """Persist atomically. We snapshot `entries` (a shallow dict copy) BEFORE
        pickling so a concurrent `record()` on another thread can't mutate the
        dict mid-iteration ("dictionary changed size during iteration") — the
        per-model value dicts are never mutated in place, so a shallow copy is a
        safe, cheap point-in-time view. The pickle is written to a temp file and
        atomically renamed, so a crash or a killed run can never leave a
        half-written, unloadable cache behind."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        snapshot = dict(self.entries)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as fh:
            pickle.dump(snapshot, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
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

    def peek(self, source: str, car, pages: int) -> Optional[dict]:
        # Live always fetches; nothing is served from cache.
        return None

    def retrieve(self, source: str, car, pages: int,
                 deadline: Optional[float] = None) -> RetrieveResult:
        fn = retrieve_autobazar if source == "autobazar" else retrieve_bazos
        t0 = time.perf_counter()
        reqs_before = http_client.request_count()
        df, err = fn(car, pages, self.delay, deadline)
        # True number of network GETs issued (may be 1..pages, or more with a
        # keyword fallback) — not the previous hardcoded 1.
        http_reqs = http_client.request_count() - reqs_before
        elapsed = time.perf_counter() - t0
        err = err or ""
        if self.store is not None:
            self.store.record(source, car.brand, car.model, pages, df, err, elapsed)
        return RetrieveResult(df=df, err=err, elapsed_s=elapsed, http_requests=http_reqs)


class CachedMarketProvider:
    """
    Zero-HTTP replay from a MarketStore. NEVER calls Autobazar or Bazoš. On a
    miss it returns an empty pool with a CACHE_MISS marker (a non-blocking
    error) so the pipeline still runs end-to-end deterministically.
    """

    is_live = False

    def __init__(self, store: MarketStore):
        self.store = store

    def peek(self, source: str, car, pages: int) -> Optional[dict]:
        hit = self.store.get(source, car.brand, car.model, pages)
        if hit is None:
            return None
        return {"from_cache": True, "age_s": MarketStore.age_of(hit)}

    def retrieve(self, source: str, car, pages: int,
                 deadline: Optional[float] = None) -> RetrieveResult:
        # Zero-HTTP replay: the per-model deadline is irrelevant (no network).
        hit = self.store.get(source, car.brand, car.model, pages)
        if hit is None:
            return RetrieveResult(pd.DataFrame(), CACHE_MISS_ERR, 0.0, 0)
        # Copy so downstream mutations never poison the shared snapshot.
        return RetrieveResult(
            hit["df"].copy(deep=True), hit["err"], 0.0, 0,
            from_cache=True, age_s=MarketStore.age_of(hit),
        )


class PersistentCacheProvider:
    """
    The transport that backs Inventory mode in production: a persistent,
    cache-first market-data layer with a live fallback.

    On each retrieve:
      * If a NON-EXPIRED cached pool exists for (source, brand, model) at the
        requested depth (and we are not force-refreshing), serve it with ZERO
        HTTP requests.
      * Otherwise fetch it live (single-threaded, polite delay — the same
        Phase-6 retrieval), record it with a fresh `fetched_at`, persist the
        store to disk, and return it.

    Autobazar and Bazoš pools are stored under separate keys and never merged.
    Only normalized candidate pools are cached — never car valuations. There is
    no concurrency, proxy, or rate-limit circumvention: a miss is just one more
    ordinary polite request.
    """

    is_live = True  # may hit the network on a miss/expiry/force-refresh

    def __init__(self, store: Optional[MarketStore] = None,
                 path: str = DEFAULT_STORE_PATH,
                 ttl_s: Optional[float] = DEFAULT_TTL_S,
                 delay: float = 1.0,
                 force_refresh: bool = False):
        self.path = path
        self.store = store if store is not None else MarketStore.load_or_empty(path)
        self.ttl_s = ttl_s
        self.delay = delay
        self.force_refresh = force_refresh
        # Serializes ALL access to the shared store. Retrieval runs on worker
        # threads (see inventory_run's per-model watchdog), and an ABANDONED
        # worker may still call `record()` after the main thread has moved to the
        # next model's `peek()`. Without this lock those concurrent reads/writes
        # race the same dict. The lock is held only for fast in-memory dict ops —
        # never across a network fetch — so it cannot itself stall the run.
        self._lock = threading.RLock()

    def peek(self, source: str, car, pages: int) -> Optional[dict]:
        """Report expected cache status for this source WITHOUT fetching."""
        if self.force_refresh:
            return None
        with self._lock:
            hit = self.store.get_fresh(source, car.brand, car.model, pages, self.ttl_s)
        if hit is None:
            return None
        return {"from_cache": True, "age_s": MarketStore.age_of(hit)}

    def retrieve(self, source: str, car, pages: int,
                 deadline: Optional[float] = None) -> RetrieveResult:
        if not self.force_refresh:
            with self._lock:
                hit = self.store.get_fresh(source, car.brand, car.model, pages, self.ttl_s)
            if hit is not None:
                return RetrieveResult(
                    hit["df"].copy(deep=True), hit["err"], 0.0, 0,
                    from_cache=True, age_s=MarketStore.age_of(hit),
                )
        # Miss, expiry, or forced refresh -> live fetch (1..pages polite GETs),
        # bounded by the per-model deadline threaded down to the page loop.
        fn = retrieve_autobazar if source == "autobazar" else retrieve_bazos
        t0 = time.perf_counter()
        reqs_before = http_client.request_count()
        df, err = fn(car, pages, self.delay, deadline)
        http_reqs = http_client.request_count() - reqs_before
        elapsed = time.perf_counter() - t0
        err = err or ""
        # Record the fresh pool in memory only. We deliberately do NOT persist to
        # disk here: pickling the whole (growing) store on every live fetch is a
        # GIL-heavy operation that stalls the generator thread and gets slower as
        # the run proceeds — the "a single model appears to run for 60-70s" bug.
        # The store is persisted ONCE at the end of the run via `flush()`.
        with self._lock:
            self.store.record(source, car.brand, car.model, pages, df, err, elapsed)
        return RetrieveResult(df=df, err=err, elapsed_s=elapsed, http_requests=http_reqs,
                              from_cache=False, age_s=0.0)

    def flush(self) -> None:
        """Persist the accumulated store to disk exactly once (call after a run).
        Best-effort: a failed save must never surface as an analysis error."""
        try:
            with self._lock:
                self.store.save(self.path)
        except Exception:
            pass
