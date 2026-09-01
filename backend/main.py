"""
FastAPI service exposing the car-valuation engine to the frontend.

This is a thin transport layer. ALL comparison logic lives in the engine
package (copied verbatim from the tested `autobazar_phase1` sources) and is
never reimplemented here. Autobazar and Bazos results are kept separate by the
engine and passed through untouched.

Routes are served under `/api` directly so they match the path Vercel
forwards to this service (the `/api/*` rewrite passes the original path
through unchanged):
  GET  /api/health             - liveness probe
  POST /api/parse-row          - parse a pasted Excel/Sheets/Markdown row -> fields
  POST /api/inventory/parse    - normalize+validate an uploaded inventory file
  GET  /api/inventory/demo     - normalize+validate a bundled demo inventory
  POST /api/inventory/analyze/stream - batch market analysis as an SSE stream
  POST /api/compare            - single-car comparison (synchronous JSON)
  POST /api/compare/stream     - single-car comparison as an SSE progress stream

/api/inventory/parse and /demo do PARSING/VALIDATION ONLY — no scraping.
/api/inventory/analyze/stream, /api/compare, and /api/compare/stream all read
market comparables via the SAME provider-selection logic (see
_build_market_provider): MarketCollectorProvider — a read-only, DB-backed
bridge into market-collector's continuously-updated capture ledger (see
that sibling project) — by default, with a per-request `refresh=true` or a
per-endpoint env var (INVENTORY_MARKET_PROVIDER / COMPARE_MARKET_PROVIDER
set to "legacy") falling back to on-demand live scraping instead. No
endpoint here does live scraping by default anymore as of 2026-08-30.
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Optional

import fastapi
import fastapi.middleware.cors
from fastapi import File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# The engine modules import each other by top-level name (e.g.
# `from phase6_validate import ...`), so the engine dir must be importable
# as a flat set of modules.
_ENGINE_DIR = os.path.join(os.path.dirname(__file__), "engine")
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from single_car import (  # noqa: E402  (import after sys.path setup)
    SingleCarInput,
    build_canonical_car,
    compare_single_car,
)
from row_parser import parse_pasted_row  # noqa: E402
from inventory_api import (  # noqa: E402
    InventoryReadError,
    parse_demo,
    parse_upload,
)
from inventory_run import analyze_inventory  # noqa: E402
from market_provider import PersistentCacheProvider, MarketCollectorProvider, MARKET_DB_PATHS  # noqa: E402


app = fastapi.FastAPI(title="Car Valuation API")

app.add_middleware(
    fastapi.middleware.cors.CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# JSON sanitation: engine values can be numpy scalars / NaN, which are not
# valid JSON. Convert to native Python and turn NaN/inf into null.
# --------------------------------------------------------------------------- #
def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    # numpy scalar -> python scalar
    item = getattr(obj, "item", None)
    if callable(item) and obj.__class__.__module__ == "numpy":
        val = obj.item()
        if isinstance(val, float) and not math.isfinite(val):
            return None
        return val
    return obj


# --------------------------------------------------------------------------- #
# Request model
# --------------------------------------------------------------------------- #
class CarPayload(BaseModel):
    brand: str
    model: str
    variant: Optional[str] = None
    year: Optional[int] = None
    fuel: Optional[str] = None
    km: Optional[int] = None
    price: Optional[int] = None
    power_kw: Optional[int] = None
    transmission: Optional[str] = None
    body_type: Optional[str] = None
    # Retrieval breadth (kept modest for responsiveness). Ignored by
    # MarketCollectorProvider (see _build_market_provider) -- meaningful
    # only when refresh=true forces the live provider.
    max_pages: int = 3
    delay: float = 1.0
    refresh: bool = False  # force a live refresh, ignoring the DB-backed provider
    # Which market's DB to read (see market_provider.MARKET_DB_PATHS). "sk" is
    # the only one with real data today; not sent by the frontend yet -- see
    # that module's CZ placeholder comment. Unknown values fall back to "sk".
    market: str = "sk"


def _to_input(p: CarPayload) -> SingleCarInput:
    return SingleCarInput(
        brand=p.brand,
        model=p.model,
        variant=p.variant,
        year=p.year,
        fuel=p.fuel,
        km=p.km,
        price=p.price,
        power_kw=p.power_kw,
        transmission=p.transmission,
        body_type=p.body_type,
    )


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
# NOTE: routes are declared WITH the `/api` prefix. This backend runs as a
# Vercel service (see vercel.json `services`), and the top-level rewrite
# `"/api/(.*)" -> { service: backend }` forwards the ORIGINAL path unchanged —
# the service receives `/api/health`, NOT `/health`. Keep the `/api` prefix on
# every route below so they match what Vercel forwards.
@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Paste-a-row: parse a single Excel / Google Sheets / Markdown row into the
# canonical single-car fields. Pure parsing (no scraping) - reuses the shared
# inventory normalization layer, so it never diverges from the manual path.
# --------------------------------------------------------------------------- #
class ParseRowPayload(BaseModel):
    text: str


@app.post("/api/parse-row")
def parse_row(payload: ParseRowPayload) -> dict:
    parsed = parse_pasted_row(payload.text or "")
    return _json_safe(parsed.to_dict())


# --------------------------------------------------------------------------- #
# Inventory: upload → normalize → validate. PARSING ONLY — no scraping happens
# here. Reuses the same `inventory_normalizer` as the single-car paste path, so
# there is exactly one normalization methodology across the app. The response
# is a review report the frontend renders before any (future) valuation phase.
# --------------------------------------------------------------------------- #
@app.post("/api/inventory/parse")
async def inventory_parse(file: UploadFile = File(...)) -> dict:
    content = await file.read()
    try:
        return _json_safe(parse_upload(file.filename or "", content))
    except InventoryReadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/inventory/demo")
def inventory_demo(name: str = "sample") -> dict:
    try:
        return _json_safe(parse_demo(name))
    except InventoryReadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Inventory batch market analysis — SSE progress stream.
#
# This DOES retrieve from the marketplaces, but grouped per (brand, model) with
# a shared cache, progressive page depth, polite single-threaded pacing, and a
# circuit breaker — so requests scale with unique models, not car count. Each
# car is valued by the same shared engine as the single-car path.
# --------------------------------------------------------------------------- #
class InventoryAnalyzePayload(BaseModel):
    rows: list[dict]
    delay: float = 1.0
    max_pages: int = 3
    refresh: bool = False  # force a live refresh, ignoring cached pools
    # See CarPayload.market above -- same placeholder, not sent by the
    # frontend yet.
    market: str = "sk"


# Shared provider selection, used by BOTH /api/inventory/analyze/stream and
# (2026-08-30) /api/compare + /api/compare/stream -- previously those two
# were "untouched and always use live retrieval directly"; now they use the
# SAME DB-backed default as the inventory endpoint, via compare_single_car's
# new `provider=` seam (see single_car.py's own docstring for that).
#
# 2026-08-24: MarketCollectorProvider (the read-only bridge into
# market-collector's continuously-refreshed incremental capture ledger,
# verified end-to-end against real same-day data) is the DEFAULT for both.
# Falls back to the original live/cache provider in two cases:
#   1. `refresh=true` is requested -- MarketCollectorProvider is read-only
#      against a historical snapshot/ledger with no live-fetch capability
#      at all, so an explicit refresh request must use the live provider
#      regardless of the default.
#   2. Constructing MarketCollectorProvider raises for any reason -- most
#      likely market-collector simply isn't present on this machine/
#      deployment (exactly the "deployed backend" gap this module used to
#      warn about), caught here so that missing sibling project degrades
#      the endpoint back to its original behavior instead of crashing it.
#
# `env_var` lets each endpoint keep an INDEPENDENT legacy escape hatch
# (INVENTORY_MARKET_PROVIDER for the batch endpoint, COMPARE_MARKET_PROVIDER
# for the single-car ones) -- e.g. forcing live for spot-checks without
# affecting batch inventory runs, or vice versa. Set to "legacy" to force
# the live/cache provider unconditionally; any other value (including
# unset, or the now-redundant "market_collector") takes the default path
# above.
def _build_market_provider(*, delay: float, refresh: bool, env_var: str, log_prefix: str, market: str = "sk"):
    def _legacy():
        # A persistent, cache-first market layer: fresh (source, brand, model)
        # pools are reused across runs with ZERO HTTP; misses/expiries fetch
        # live and update the cache. `refresh=true` forces a live refresh.
        return PersistentCacheProvider(delay=delay, force_refresh=refresh)

    db_path = MARKET_DB_PATHS.get(market, MARKET_DB_PATHS["sk"])
    if refresh or os.environ.get(env_var) == "legacy":
        return _legacy()
    if not os.path.exists(db_path):
        # Expected today for market="cz" (see market_provider.py's CZ
        # placeholder comment) -- no DB there yet. Skip straight to the
        # fallback instead of letting MarketCollectorProvider's sqlite3
        # connect silently create an empty file at that path.
        if market != "sk":
            print(f"[v0][{log_prefix}] no DB for market={market!r} at {db_path} yet, "
                  f"falling back to PersistentCacheProvider.")
        return _legacy()
    try:
        return MarketCollectorProvider(db_path=db_path)
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the endpoint
        print(f"[v0][{log_prefix}] MarketCollectorProvider unavailable ({exc!r}), "
              f"falling back to PersistentCacheProvider.")
        return _legacy()


def _build_inventory_provider(payload: "InventoryAnalyzePayload"):
    return _build_market_provider(
        delay=payload.delay, refresh=payload.refresh,
        env_var="INVENTORY_MARKET_PROVIDER", log_prefix="inventory", market=payload.market,
    )


def _build_compare_provider(payload: "CarPayload"):
    return _build_market_provider(
        delay=payload.delay, refresh=payload.refresh,
        env_var="COMPARE_MARKET_PROVIDER", log_prefix="compare", market=payload.market,
    )


@app.post("/api/inventory/analyze/stream")
def inventory_analyze_stream(payload: InventoryAnalyzePayload) -> StreamingResponse:
    provider = _build_inventory_provider(payload)

    def gen():
        try:
            for event in analyze_inventory(
                payload.rows,
                delay=payload.delay,
                max_pages=payload.max_pages,
                provider=provider,
            ):
                yield _sse(event)
        except Exception as e:  # noqa: BLE001 - surface any engine error to the client
            yield _sse({"stage": "error", "label": "Analysis failed", "message": str(e)})
        finally:
            # Persist the accumulated market cache ONCE, after streaming ends, so
            # the hot path never pays for disk I/O / pickling mid-run.
            provider.flush()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------- #
# Single-car: synchronous
# --------------------------------------------------------------------------- #
@app.post("/api/compare")
def compare(payload: CarPayload) -> dict:
    provider = _build_compare_provider(payload)
    result = compare_single_car(
        _to_input(payload), max_pages=payload.max_pages, delay=payload.delay,
        provider=provider,
    )
    provider.flush()
    return _json_safe(result)


# --------------------------------------------------------------------------- #
# Single-car: SSE progress stream.
#
# The stages are REAL: each event is emitted around an actual engine step
# (build -> retrieve Autobazar -> retrieve Bazos -> compare). The final event
# carries the full source-separated result produced by the shared engine.
# --------------------------------------------------------------------------- #
def _sse(payload: dict) -> str:
    return f"data: {json.dumps(_json_safe(payload), ensure_ascii=False)}\n\n"


@app.post("/api/compare/stream")
def compare_stream(payload: CarPayload) -> StreamingResponse:
    inp = _to_input(payload)
    provider = _build_compare_provider(payload)

    def gen():
        try:
            yield _sse({"stage": "preparing", "label": "Preparing vehicle"})
            car = build_canonical_car(inp)
            yield _sse(
                {
                    "stage": "prepared",
                    "label": "Preparing vehicle",
                    "car": {
                        "brand": car.brand,
                        "model": car.model,
                        "variant_engine": car.variant_engine,
                        "fuel": car.fuel,
                        "year": car.year,
                        "km": car.km,
                        "power_kw": car.power_kw,
                        "transmission": car.transmission,
                        "body_type": car.body_type,
                        "asking_price_eur": car.price,
                        "power_source": car.power_source,
                    },
                }
            )

            yield _sse({"stage": "searching_autobazar", "label": "Searching Autobazar.eu"})
            ab_rr = provider.retrieve("autobazar", car, payload.max_pages)
            ab_df, ab_err = ab_rr.df, ab_rr.err
            yield _sse(
                {
                    "stage": "autobazar_done",
                    "label": "Searching Autobazar.eu",
                    "raw_count": int(len(ab_df)),
                    "error": ab_err or None,
                }
            )

            yield _sse({"stage": "searching_bazos", "label": "Searching Bazos.sk"})
            bz_rr = provider.retrieve("bazos", car, payload.max_pages)
            bz_df, bz_err = bz_rr.df, bz_rr.err
            yield _sse(
                {
                    "stage": "bazos_done",
                    "label": "Searching Bazos.sk",
                    "raw_count": int(len(bz_df)),
                    "error": bz_err or None,
                }
            )

            yield _sse({"stage": "comparing", "label": "Comparing listings"})
            result = compare_single_car(
                inp, retrieved=(ab_df, ab_err or "", bz_df, bz_err or "")
            )

            yield _sse({"stage": "finalizing", "label": "Preparing results"})
            yield _sse({"stage": "result", "label": "Done", "result": _json_safe(result)})
        except Exception as e:  # noqa: BLE001 - surface any engine error to the client
            yield _sse({"stage": "error", "label": "Comparison failed", "message": str(e)})
        finally:
            provider.flush()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
