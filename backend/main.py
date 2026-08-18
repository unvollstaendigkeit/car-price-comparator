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
/api/inventory/analyze/stream DOES retrieve from the marketplaces, reusing the
same engine + polite pacing as the single-car path (grouped per brand+model).
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
from phase6_validate import retrieve_autobazar, retrieve_bazos  # noqa: E402
from row_parser import parse_pasted_row  # noqa: E402
from inventory_api import (  # noqa: E402
    InventoryReadError,
    parse_demo,
    parse_upload,
)
from inventory_run import analyze_inventory  # noqa: E402
from market_provider import PersistentCacheProvider, MarketCollectorProvider  # noqa: E402


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
    # Retrieval breadth (kept modest for responsiveness).
    max_pages: int = 3
    delay: float = 1.0


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


# Explicit provider selection for THIS endpoint only (/api/compare and
# /api/compare/stream are untouched and always use live retrieval directly).
# Default (unset, or anything other than "market_collector") preserves the
# CURRENT PersistentCacheProvider behavior exactly -- never changes silently.
# Only INVENTORY_MARKET_PROVIDER=market_collector switches to the read-only,
# offline, single-pinned-snapshot MarketCollectorProvider; there is no
# automatic fallback between the two in either direction.
def _build_inventory_provider(payload: "InventoryAnalyzePayload"):
    if os.environ.get("INVENTORY_MARKET_PROVIDER") == "market_collector":
        return MarketCollectorProvider()
    # A persistent, cache-first market layer: fresh (source, brand, model) pools
    # are reused across runs with ZERO HTTP; misses/expiries fetch live and
    # update the cache. `refresh=true` forces a live refresh of every model.
    return PersistentCacheProvider(delay=payload.delay, force_refresh=payload.refresh)


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
    result = compare_single_car(
        _to_input(payload), max_pages=payload.max_pages, delay=payload.delay
    )
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
            ab_df, ab_err = retrieve_autobazar(car, payload.max_pages, payload.delay)
            yield _sse(
                {
                    "stage": "autobazar_done",
                    "label": "Searching Autobazar.eu",
                    "raw_count": int(len(ab_df)),
                    "error": ab_err or None,
                }
            )

            yield _sse({"stage": "searching_bazos", "label": "Searching Bazos.sk"})
            bz_df, bz_err = retrieve_bazos(car, payload.max_pages, payload.delay)
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

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
