"""
Phase 1 proof-of-concept: retrieve and parse ONE Autobazar.eu search-result page.

Scope (deliberately small):
  - one search, one result page
  - public search-result pages only
  - NO anti-bot circumvention of any kind

If Autobazar.eu blocks the request or shows an anti-bot challenge, this script
STOPS and reports exactly what happened (see `BlockedError`). It never tries to
bypass CAPTCHA / Cloudflare / rate limits / fingerprinting / proxies.

Why this parser is shaped the way it is
----------------------------------------
Autobazar.eu is a Next.js application. Every search-result page embeds a
`<script id="__NEXT_DATA__" type="application/json">` blob containing the
listings as clean, already-typed JSON under:

    props.pageProps.searchRecords.data  ->  list[ listing_dict ]

Parsing that JSON is far more resilient than scraping HTML <div>s, so it is the
primary path. A best-effort HTML fallback is included in case the embedded JSON
ever disappears; the text-normalisation helpers (km / € / year) exist mainly for
that fallback and to defensively coerce anything that is not already an int.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

try:
    import pandas as pd
except ImportError:  # pandas is optional for a raw run, required to build the table
    pd = None

import http_client  # bounded, instrumented GET (hard wall-clock deadline)
from http_client import FetchTimeout  # noqa: F401 - re-exported for callers


BASE = "https://www.autobazar.eu"

# A plain, honest desktop browser User-Agent. This is normal HTTP identification,
# NOT fingerprint spoofing: we do not rotate it, forge cookies, or fake TLS.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
    "Accept-Language": "sk,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# The columns requested for the Phase 1 output table, in order.
OUTPUT_COLUMNS = [
    "title",
    "year",
    "km",
    "price",
    "fuel",
    "transmission",
    "power",
    "body_type",
    "listing_date",
    "url",
]


class BlockedError(RuntimeError):
    """Raised when the site appears to block us or present an anti-bot challenge.

    We raise (and stop) instead of attempting any workaround.
    """


class NotFoundError(BlockedError):
    """Raised when a specific URL returns HTTP 404 or 410 -- a definitive,
    terminal "this page does not exist" response, not a block/rate-limit
    signal. IS a BlockedError subclass -- deliberately, so every existing
    `except BlockedError` call site (search-page fetches, where a 404/410
    would be a genuine anomaly, not "this listing is gone") keeps stopping
    and reporting exactly as before, with no call-site changes needed.
    Detail-page callers that want the precise "confirmed gone" outcome
    (record it and never retry, rather than stop-and-report) should catch
    NotFoundError specifically, BEFORE any broader `except BlockedError`.

    2026-08-24 fix: fetch() previously funneled 404/410 into the generic
    `status != 200` -> BlockedError fallback, indistinguishable from an
    actual block. Detail-page adapters caught BlockedError as a transient
    failure and left the item in the capture backlog to retry forever, so a
    genuinely removed listing could never drain from the queue.
    """


# --------------------------------------------------------------------------- #
# URL construction
# --------------------------------------------------------------------------- #
def build_search_url(
    keyword: str | None = None,
    brand: str | None = None,
    model: str | None = None,
    page: int = 1,
) -> str:
    """Construct a public Autobazar.eu search-result URL.

    Two supported forms (discovered by inspecting the live site):
      - free-text keyword search:  /vysledky/?keyword=Škoda Kodiaq 2.0 TDI
      - path-based brand/model:     /vysledky/osobne-vozidla/{brand}/{model}/
        (the site may 308-redirect this into a category path, e.g. SUV; that is
         handled automatically because we follow redirects.)

    `keyword` takes precedence when provided.
    """
    params = []
    if page and page > 1:
        params.append(f"page={page}")

    if keyword:
        url = f"{BASE}/vysledky/?keyword={quote(keyword)}"
        if params:
            url += "&" + "&".join(params)
        return url

    if brand:
        slug = _slugify(brand)
        path = f"/vysledky/osobne-vozidla/{slug}/"
        if model:
            path += f"{_slugify(model)}/"
        url = f"{BASE}{path}"
        if params:
            url += "?" + "&".join(params)
        return url

    raise ValueError("Provide either `keyword`, or at least `brand`.")


def _slugify(value: str) -> str:
    """Rough slug used by Autobazar paths (e.g. 'Alfa Romeo' -> 'alfa-romeo')."""
    value = value.strip().lower()
    replacements = {
        "á": "a", "ä": "a", "č": "c", "ď": "d", "é": "e", "í": "i",
        "ĺ": "l", "ľ": "l", "ň": "n", "ó": "o", "ô": "o", "ŕ": "r",
        "š": "s", "ť": "t", "ú": "u", "ý": "y", "ž": "z",
    }
    value = "".join(replacements.get(ch, ch) for ch in value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value


# --------------------------------------------------------------------------- #
# Fetching (with explicit anti-bot detection -> stop, never bypass)
# --------------------------------------------------------------------------- #
def fetch(url: str, timeout: int = 30, *, brand: str = "", model: str = "",
          page: int = 0, deadline: float | None = None) -> str:
    """GET a single page with ordinary HTTP. Detect blocking and STOP if seen.

    Returns the response body (HTML). Raises BlockedError with a clear
    explanation if the site appears to challenge or block the request.

    The GET is hard-bounded by http_client (wall-clock deadline, capped
    redirects/size, no retries). A slow/hanging server surfaces as FetchTimeout
    (a SOFT, non-block failure) and propagates to the caller so it can keep any
    partial results and continue; a connection error still maps to BlockedError.

    `deadline` (absolute time.perf_counter() value, optional) is the per-model
    time budget: when given, this single request's connect/read/body ceilings
    are shrunk to the time remaining so it can never overshoot the budget. If no
    time is left, we raise FetchTimeout immediately without touching the network.
    """
    bounds: dict = {}
    if deadline is not None:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise FetchTimeout(f"{url!r}: model time budget exhausted before request")
        bounds = http_client.bounds_for_remaining(remaining)

    try:
        status, body, resp_headers = http_client.get_bounded(
            url, HEADERS, source="autobazar", brand=brand, model=model, page=page,
            **bounds,
        )
    except FetchTimeout:
        raise  # non-block soft failure; do NOT convert to BlockedError
    except requests.RequestException as exc:
        raise BlockedError(f"Network error retrieving {url!r}: {exc}") from exc

    body = body or ""
    bot_header = resp_headers.get("x-bot-request")

    # 1) HTTP-level blocking signals.
    if status in (401, 403, 429) or status >= 500:
        raise BlockedError(
            f"Request to {url!r} returned HTTP {status}. "
            f"x-bot-request={bot_header!r}. Stopping without any bypass attempt. "
            f"This may be rate-limiting or an access restriction."
        )

    # 2) Explicit anti-bot / challenge markers in headers or body.
    if bot_header and bot_header.lower() not in ("false", "0", ""):
        raise BlockedError(
            f"Site flagged this request as a bot (x-bot-request={bot_header!r}) "
            f"for {url!r}. Stopping — no circumvention will be attempted."
        )

    lowered = body[:5000].lower()
    challenge_markers = (
        "cf-challenge", "cf-turnstile", "just a moment",
        "captcha", "attention required", "access denied",
        "please enable javascript and cookies",
    )
    hit = next((m for m in challenge_markers if m in lowered), None)
    if hit:
        raise BlockedError(
            f"Anti-bot challenge detected (marker: {hit!r}) at {url!r}. "
            f"Stopping — no CAPTCHA/Cloudflare bypass will be attempted."
        )

    # 3) Definitive "gone" -- distinct from a block, and from the generic
    # fallback below: a removed/expired listing, not a failure to retry.
    if status in (404, 410):
        raise NotFoundError(f"{url!r} returned HTTP {status} (listing not found/gone).")

    if status != 200:
        raise BlockedError(
            f"Unexpected HTTP {status} for {url!r} (x-bot-request={bot_header!r})."
        )

    return body


# --------------------------------------------------------------------------- #
# Text normalisation helpers (resilient to missing / oddly-formatted values)
# --------------------------------------------------------------------------- #
def clean_int(value: Any) -> int | None:
    """Coerce mileage / price / power to an int, tolerating messy text.

    Examples:
        '117 057 km'  -> 117057
        '29 990 €'    -> 29990
        '110 kW'      -> 110
        160456        -> 160456
        None / ''     -> None   (leave null rather than guessing)
    """
    if value is None:
        return None
    if isinstance(value, bool):  # guard: bool is a subclass of int
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    # Keep digits only; drop spaces, non-breaking spaces, €, km, kW, commas, etc.
    digits = re.sub(r"[^\d]", "", value.replace("\xa0", " "))
    return int(digits) if digits else None


def clean_year(value: Any) -> int | None:
    """Extract a 4-digit year (1900-2099). Returns None if not present/plausible."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        y = int(value)
        return y if 1900 <= y <= 2099 else None
    if isinstance(value, str):
        m = re.search(r"(19|20)\d{2}", value)
        if m:
            return int(m.group(0))
    return None


def clean_text(value: Any) -> str | None:
    """Trim whitespace; return None for empty/missing values (never guess)."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.replace("\xa0", " ").strip()
    return value or None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _extract_next_data(html: str) -> dict | None:
    """Pull and parse the embedded Next.js __NEXT_DATA__ JSON, if present."""
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag is None or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except json.JSONDecodeError:
        return None


def _record_to_row(rec: dict) -> dict:
    """Map one Autobazar listing record (from JSON) to our output schema.

    Missing fields are left as None rather than guessed.
    """
    sef = rec.get("sefName")
    rec_id = rec.get("id")
    url = f"{BASE}/detail/{sef}/{rec_id}/" if sef and rec_id else None

    return {
        "title": clean_text(rec.get("title")),
        # Prefer explicit yearValue; fall back to a year-looking field if needed.
        "year": clean_year(rec.get("yearValue")),
        "km": clean_int(rec.get("mileage")),
        # finalPrice/priceCurrent are NOT interchangeable mirrors of price --
        # verified against 26,971 real collected records (2026-08-19): price
        # disagrees with finalPrice in 27.3% of them, in two distinct ways:
        #   (1) price is a pre-discount/list price (finalPrice ~3-30% lower)
        #       -- finalPrice is the actual current advertised price.
        #   (2) for listings aggregated from Autobazar's Czech-market sister
        #       site (confirmed via raw_json.location.parentNames containing
        #       "Česká republika"), price is denominated in Czech Koruna
        #       while finalPrice/priceCurrent are already EUR-converted --
        #       ratio ~24.2-24.3x, matching the real CZK/EUR exchange rate,
        #       not a discount. Using raw `price` here previously produced
        #       listings priced at >EUR 1,000,000 for ordinary cars.
        # finalPrice and priceCurrent agreed in every observed case where
        # both were present, so either is a safe primary; finalPrice is
        # preferred as the more explicitly-named field, priceCurrent as a
        # fallback, and raw price only as a last resort when neither of the
        # normalized fields is present (rare: 8/26,971 observed records).
        "price": clean_int(
            rec.get("finalPrice")
            if rec.get("finalPrice") is not None
            else rec.get("priceCurrent")
            if rec.get("priceCurrent") is not None
            else rec.get("price")
        ),
        "fuel": clean_text(rec.get("fuelValue")),
        "transmission": clean_text(rec.get("gearboxValue")),
        "power": clean_int(rec.get("enginePower")),  # kW
        "body_type": clean_text(rec.get("bodyworkValue")),
        "listing_date": clean_text(rec.get("clientUpdatedAt")),
        # clientUpdatedAt (== listing_date above, == sitemap lastmod) is a
        # bump/touch signal, NOT a reliable original-posting date -- verified
        # against 27,028 real collected records (2026-08-19): records
        # captured in the same minute had near-identical clientUpdatedAt
        # regardless of true listing age. clientCreatedAt (falling back to
        # publishUp, which matched it in 27,027/27,028 = 99.996% of those
        # same records) instead spans 47 distinct days back to 2015-01-21 --
        # the shape of genuine, varied posting dates. Stored as a first-class
        # field for later age analysis only; NOT used anywhere as a capture
        # or change-detection signal.
        "client_created_at": clean_text(
            rec.get("clientCreatedAt")
            if rec.get("clientCreatedAt") is not None
            else rec.get("publishUp")
        ),
        "url": url,
    }


def parse_listings(html: str) -> tuple[list[dict], str]:
    """Parse one result page into a list of rows.

    Returns (rows, source) where `source` is 'next_data' or 'html_fallback'.
    """
    data = _extract_next_data(html)
    if data:
        try:
            records = data["props"]["pageProps"]["searchRecords"]["data"]
        except (KeyError, TypeError):
            records = None
        if isinstance(records, list):
            return [_record_to_row(r) for r in records], "next_data"

    # Fallback: best-effort HTML parse (kept intentionally minimal). This path is
    # only reached if the embedded JSON structure ever changes/disappears.
    return _parse_html_fallback(html), "html_fallback"


def _parse_html_fallback(html: str) -> list[dict]:
    """Very small HTML fallback: find detail links + nearby price/km text.

    Intentionally conservative: it fills what it can and leaves the rest null.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    seen: set[str] = set()
    for a in soup.select('a[href^="/detail/"]'):
        href = a.get("href", "")
        if not href or href in seen:
            continue
        seen.add(href)
        block_text = a.get_text(" ", strip=True)
        # Search a small neighbourhood for price/km if the anchor text is sparse.
        container = a.find_parent()
        ctx = container.get_text(" ", strip=True) if container else block_text
        price_m = re.search(r"([\d\s\xa0]+)\s*€", ctx)
        km_m = re.search(r"([\d\s\xa0]+)\s*km", ctx)
        rows.append({
            "title": clean_text(block_text) or None,
            "year": clean_year(ctx),
            "km": clean_int(km_m.group(1)) if km_m else None,
            "price": clean_int(price_m.group(1)) if price_m else None,
            "fuel": None,
            "transmission": None,
            "power": None,
            "body_type": None,
            "listing_date": None,
            "url": BASE + href,
        })
    return rows


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
@dataclass
class ScrapeResult:
    rows: list[dict]
    source: str
    url: str


def scrape_one_page(
    keyword: str | None = None,
    brand: str | None = None,
    model: str | None = None,
    page: int = 1,
    delay: float = 0.0,
) -> ScrapeResult:
    """Fetch + parse exactly ONE search-result page."""
    url = build_search_url(keyword=keyword, brand=brand, model=model, page=page)
    if delay:
        time.sleep(delay)
    html = fetch(url)
    rows, source = parse_listings(html)
    return ScrapeResult(rows=rows, source=source, url=url)


def to_dataframe(rows: list[dict]):
    if pd is None:
        raise ImportError("pandas is not installed; `uv pip install pandas`.")
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 1: scrape ONE Autobazar.eu search-result page."
    )
    parser.add_argument(
        "--keyword", default="Škoda Kodiaq 2.0 TDI",
        help="Free-text search (default: 'Škoda Kodiaq 2.0 TDI').",
    )
    parser.add_argument("--brand", default=None, help="Brand (path-based search).")
    parser.add_argument("--model", default=None, help="Model (path-based search).")
    parser.add_argument("--page", type=int, default=1, help="Result page (default 1).")
    parser.add_argument("--csv", default="autobazar_phase1_results.csv",
                        help="Output CSV path.")
    args = parser.parse_args(argv)

    keyword = None if (args.brand or args.model) else args.keyword

    try:
        result = scrape_one_page(
            keyword=keyword, brand=args.brand, model=args.model, page=args.page
        )
    except BlockedError as exc:
        print("\n*** STOPPED: possible block / anti-bot challenge ***", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2

    df = to_dataframe(result.rows)
    df.to_csv(args.csv, index=False)

    # --- report -----------------------------------------------------------
    print(f"Search URL : {result.url}")
    print(f"Parse source: {result.source}")
    print(f"Rows extracted: {len(df)}")
    print(f"Saved CSV  : {args.csv}\n")

    with pd.option_context("display.max_columns", None, "display.width", 200):
        preview = df.copy()
        preview["title"] = preview["title"].str.slice(0, 42)
        print(preview.to_string(index=True))

    # Field-availability summary (which fields were successfully extracted).
    print("\nField availability (non-null / total):")
    for col in OUTPUT_COLUMNS:
        non_null = df[col].notna().sum()
        print(f"  {col:14}: {non_null}/{len(df)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
