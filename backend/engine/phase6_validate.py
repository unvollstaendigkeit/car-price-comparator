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
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from bs4 import BeautifulSoup

import autobazar_scraper as ab
import bazos_scraper as bz
from inventory_loader import load_inventory
from phase5_compare import (
    RuleSet, STRICT, MODERATE, BROAD,
    normalize_engine, normalize_fuel_with_title_fallback,
    normalize_transmission, normalize_drivetrain,
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

# --- Autobazar category-path slug mapping (verified against the live site) --- #
# The inventory uses short/Danish brand names; Autobazar's SEF path uses full
# slugs. Confirmed by probing brandSef in the site's embedded searchQuery.
_BRAND_SLUG = {
    "vw": "volkswagen",
    "mercedes": "mercedes-benz",
    "merc": "mercedes-benz",
}
# Small set of known model-slug quirks kept as a fast path / offline fallback.
# The live facet resolver below is the primary, general mechanism; this map only
# short-circuits well-known cases and lets tests run without network.
_MODEL_SLUG = {
    ("volkswagen", "id.3"): "id3",
    ("volkswagen", "id.4"): "id4",
    ("volkswagen", "id.5"): "id5",
}


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def _norm_label(s: str) -> str:
    """Normalize a model label/name for matching: lowercase, de-accent, collapse."""
    s = _strip_accents(str(s).lower()).strip()
    return re.sub(r"\s+", " ", s)


# Cache of {brand_slug: {normalized_label: model_sef}} scraped once per brand
# from Autobazar's own model facet in __NEXT_DATA__. This is the general,
# self-maintaining source of truth for model slugs (e.g. "3 Series" -> "rad-3",
# "A-Class" -> "a-trieda") rather than a brittle hand-maintained table.
_AB_MODEL_FACET_CACHE: dict[str, dict[str, str]] = {}


def _ab_brand_model_facet(brand_slug: str) -> dict[str, str]:
    """Fetch and cache Autobazar's model taxonomy for a brand: label -> sefName."""
    if brand_slug in _AB_MODEL_FACET_CACHE:
        return _AB_MODEL_FACET_CACHE[brand_slug]
    facet: dict[str, str] = {}
    try:
        html = ab.fetch(f"{ab.BASE}/vysledky/osobne-vozidla/{brand_slug}/")
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            re.S,
        )
        if m:
            data = json.loads(m.group(1))

            def walk(o):
                if isinstance(o, dict):
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for x in o:
                        if (
                            isinstance(x, dict)
                            and {"label", "sefName"} <= set(x.keys())
                            and "subs" in x
                            and x.get("label")
                            and x.get("sefName")
                        ):
                            facet[_norm_label(x["label"])] = x["sefName"]
                        walk(x)

            walk(data)
    except Exception:  # noqa: BLE001 - resolver must degrade gracefully to fallback
        pass
    _AB_MODEL_FACET_CACHE[brand_slug] = facet
    return facet


def _model_slug_candidates(model: str) -> list[str]:
    """
    Deterministic candidate slugs for a model name, in priority order. Used to
    match against the live facet labels AND as constructed slugs to try directly.
    Handles: 'N Series' -> 'rad n', '-Class'/'-Klasse' -> ' trieda' / stripped,
    and 'ID.3' -> 'id3'.
    """
    m = _norm_label(model)
    cands = [m]
    ser = re.match(r"^(\d+)\s*series$", m)
    if ser:
        cands += [f"rad {ser.group(1)}", f"rad-{ser.group(1)}"]
    if m.endswith("-class") or m.endswith(" class"):
        base = m.rsplit("class", 1)[0].strip(" -")
        cands += [f"{base} trieda", base]
    if m.endswith("-klasse") or m.endswith(" klasse"):
        base = m.rsplit("klasse", 1)[0].strip(" -")
        cands += [f"{base} trieda", base]
    # de-dotted compact form, e.g. id.3 -> id3
    compact = re.sub(r"[.\s-]+", "", m)
    if compact and compact != m:
        cands.append(compact)
    # unique, order-preserving
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


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
    # Optional + defaulted (unlike every field above) so existing call sites
    # (tests, other callers) that predate this field keep working unchanged --
    # drivetrain is legitimately unknown for the vast majority of listings.
    drivetrain: Optional[str] = None

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
        drivetrain=_clean(row.get("drivetrain")),
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
            "fuel": normalize_fuel_with_title_fallback(raw.get("fuel"), title),
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
        "fuel": normalize_fuel_with_title_fallback(raw.get("fuel"), title),
        "year": _int(raw.get("year")),
        "km": _int(raw.get("km")),
        "power_kw": _int(raw.get("power")),
        "transmission": normalize_transmission(raw.get("transmission")),
        "body_type": _clean(raw.get("body_type")),
        "price": _int(raw.get("price")),
        "url": _clean(raw.get("url")),
    }


def _ab_slugs(car: InvCar, use_live_facet: bool = True) -> tuple[str, str]:
    """
    Map an inventory brand/model to Autobazar category-path SEF slugs.

    Resolution order (first hit wins):
      1. Known-quirk hardcoded map (fast path, offline-friendly).
      2. Live model facet from Autobazar's own taxonomy: match the model's
         candidate labels (e.g. '3 series' -> facet label 'rad 3' -> 'rad-3',
         'a-class' -> 'a trieda' -> 'a-trieda').
      3. Constructed candidate slug that the facet confirms exists.
      4. Generic slugify (last resort; retrieve_autobazar keyword-falls-back if
         this 404s).
    """
    brand_key = car.brand.strip().lower()
    brand_slug = _BRAND_SLUG.get(brand_key, ab._slugify(car.brand))

    hard = _MODEL_SLUG.get((brand_slug, car.model.strip().lower()))
    if hard:
        return brand_slug, hard

    candidates = _model_slug_candidates(car.model)

    if use_live_facet:
        facet = _ab_brand_model_facet(brand_slug)
        if facet:
            # (a) a candidate label matches a facet label -> use its real sef
            for c in candidates:
                if c in facet:
                    return brand_slug, facet[c]
            # (b) a constructed slug equals a known facet sef value
            facet_sefs = set(facet.values())
            for c in candidates:
                cs = ab._slugify(c)
                if cs in facet_sefs:
                    return brand_slug, cs

    return brand_slug, ab._slugify(car.model)


def _budgeted_sleep(delay: float, deadline: Optional[float]) -> None:
    """Polite inter-page sleep that never runs past the per-model deadline."""
    if deadline is None:
        time.sleep(delay)
        return
    remaining = deadline - time.perf_counter()
    if remaining > 0:
        time.sleep(min(delay, remaining))


def _fetch_ab_pages(url_for_page, max_pages: int, delay: float,
                    brand: str = "", model: str = "",
                    deadline: Optional[float] = None) -> tuple[list[dict], Optional[str]]:
    """Fetch up to `max_pages` Autobazar pages.

    Returns (rows, timeout_note). A hard per-request FetchTimeout is a SOFT
    failure: we stop paginating but KEEP the pages already gathered and report
    a TIMEOUT note (never a block). BlockedError still propagates to the caller.

    `deadline` (absolute time.perf_counter(), optional) is the per-model time
    budget. We check it BEFORE every page GET so we stop the moment the budget
    is spent (keeping the pages already fetched), and we never sleep past it.
    """
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        if deadline is not None and time.perf_counter() >= deadline:
            return rows, (f"TIMEOUT: model time budget exceeded after {page - 1} "
                          f"page(s); kept {len(rows)} listing(s)")
        try:
            html = ab.fetch(url_for_page(page), brand=brand, model=model,
                            page=page, deadline=deadline)
        except ab.FetchTimeout as e:
            return rows, f"TIMEOUT: page {page} exceeded the deadline ({e})"
        listings, _mode = ab.parse_listings(html)
        if not listings:
            break
        rows.extend(_norm_market_row("autobazar", r) for r in listings)
        if page < max_pages:
            _budgeted_sleep(delay, deadline)
    return rows, None


def retrieve_autobazar(car: InvCar, max_pages: int, delay: float,
                       deadline: Optional[float] = None) -> tuple[pd.DataFrame, Optional[str]]:
    """
    Retrieve Autobazar candidates via the CATEGORY PATH
    (/vysledky/osobne-vozidla/{brand}/{model}/), which is a genuine server-side
    brand+model filter and is far cleaner than keyword search (Phase 2B finding:
    near-zero spare-parts noise).

    Resilience: if the category slug 404s (an unmapped model-name quirk), we fall
    back to the keyword search for that one car so it never silently returns zero.
    The mode actually used is reported back to the caller for the log.
    """
    brand_slug, model_slug = _ab_slugs(car)

    def cat_url(page: int) -> str:
        base = f"{ab.BASE}/vysledky/osobne-vozidla/{brand_slug}/{model_slug}/"
        return base if page <= 1 else f"{base}?page={page}"

    note: Optional[str] = None
    tnote: Optional[str] = None
    try:
        rows, tnote = _fetch_ab_pages(cat_url, max_pages, delay, car.brand, car.model, deadline)
    except ab.BlockedError as e:
        # 404 = wrong slug -> keyword fallback; anything else is a real stop.
        if "HTTP 404" not in str(e):
            return pd.DataFrame(), f"BLOCKED: {e}"
        # Only spend a fallback (another set of GETs) if the budget allows it.
        if deadline is not None and time.perf_counter() >= deadline:
            return pd.DataFrame(), (f"TIMEOUT: model time budget exceeded before "
                                    f"keyword fallback for {brand_slug}/{model_slug}")
        note = f"category slug {brand_slug}/{model_slug} 404 -> keyword fallback"
        _budgeted_sleep(delay, deadline)
        keyword = f"{car.brand} {car.model}"
        try:
            rows, tnote = _fetch_ab_pages(
                lambda p: ab.build_search_url(keyword=keyword, page=p), max_pages, delay,
                car.brand, car.model, deadline,
            )
        except ab.BlockedError as e2:
            return pd.DataFrame(), f"BLOCKED (fallback): {e2}"
        except Exception as e2:  # noqa: BLE001
            return pd.DataFrame(), f"ERROR (fallback): {type(e2).__name__}: {e2}"
    except Exception as e:  # noqa: BLE001 - surface, never hide
        return pd.DataFrame(), f"ERROR: {type(e).__name__}: {e}"

    # A timeout that still yielded partial rows is reported (non-block) but the
    # data is kept and used; combine it with any fallback note.
    note = "; ".join(n for n in (note, tnote) if n) or None

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset="url").reset_index(drop=True)
        return df, note
    # Fetched OK but empty -> genuine no-data (record the slug/mode we tried), NOT
    # a scraper failure. Keeps any existing fallback/timeout note appended.
    no_data = f"no listings at category {brand_slug}/{model_slug}"
    return df, (f"{note}; {no_data}" if note else no_data)


def _bazos_query(car: InvCar) -> str:
    """
    Build the Bazos free-text query. Bazos matches the raw string against ad
    titles, so decorative model suffixes hurt recall: 'Mercedes GLA-Class'
    returns ~5 ads where 'Mercedes GLA' returns a full page. We therefore strip
    '-Class'/'-Klasse'/' Series' and split a glued 'GLA-Class' on the hyphen,
    keeping just the model stem. Brand+model only; year/trim would over-narrow.
    """
    model = _strip_accents(car.model).strip()
    model = re.sub(r"[-\s]?(class|klasse|series|trieda)\b", "", model, flags=re.I)
    model = model.replace("-", " ").strip()
    model = re.sub(r"\s+", " ", model)
    return f"{car.brand} {model}".strip()


def retrieve_bazos(car: InvCar, max_pages: int, delay: float,
                   deadline: Optional[float] = None) -> tuple[pd.DataFrame, Optional[str]]:
    """
    Retrieve Bazos candidates by normalized free-text query.

    Failure vs no-data: a network/HTTP block returns an empty frame with a
    BLOCKED note (a real error the caller must surface). A successful fetch that
    simply has no matching ads returns an empty frame with a 'no listings' note,
    NOT an error - these are semantically different and were previously
    conflated, making genuine market scarcity look like a scraper failure.
    """
    query = _bazos_query(car)
    rows: list[dict] = []
    pages_ok = 0
    tnote: Optional[str] = None
    try:
        for page in range(1, max_pages + 1):
            if deadline is not None and time.perf_counter() >= deadline:
                tnote = (f"TIMEOUT: model time budget exceeded after {page - 1} "
                         f"page(s); kept {len(rows)} listing(s)")
                break
            url = bz.build_search_url(query, crp=bz.crp_for_page(page))
            try:
                html = bz.fetch(url, brand=car.brand, model=car.model,
                                page=page, deadline=deadline)
            except bz.FetchTimeout as e:
                # SOFT failure: stop paginating, KEEP partial rows, report a
                # non-block TIMEOUT note (must not trip the circuit breaker).
                tnote = f"TIMEOUT: page {page} exceeded the deadline ({e})"
                break
            pages_ok += 1
            cards = bz.parse_page(html, query, page)
            if not cards:
                break
            rows.extend(_norm_market_row("bazos", r) for r in cards)
            if page < max_pages:
                _budgeted_sleep(delay, deadline)
    except bz.BlockedError as e:
        return pd.DataFrame(rows), f"BLOCKED: {e}"
    except Exception as e:  # noqa: BLE001
        return pd.DataFrame(rows), f"ERROR: {type(e).__name__}: {e}"

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset="listing_id").reset_index(drop=True)
        return df, tnote  # tnote carries a timeout note if we kept partial data
    # Fetched fine but nothing came back -> genuine no-data, not a failure.
    no_data = f"no listings for query '{query}' (fetched {pages_ok} page(s) OK)"
    return df, (f"{tnote}; {no_data}" if tnote else no_data)


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


# Distinct model-line markers that must NOT be treated as the searched model,
# even though the model string is a literal substring of the title (e.g. a
# "Passat CC" ad's title does contain "Passat") or the title's own model
# candidate would otherwise resolve as a match (e.g. "Proace Verso" contains
# "Verso"). Each entry is a genuinely different product line, not a trim/badge
# of the same car. SUFFIXES matches "{model} {marker}" (space-separated,
# confirmed 2026-08-17: Passat CC/Alltrack contaminating a Passat pool; Golf
# Sportsvan is its own market-collector collection target) OR "{model}{marker}"
# FUSED with no space (added 2026-08-28 -- Autobazar's own taxonomy names
# "XCeed" and "CLA"/"CLE"/"CLS" this way, no space at all, so the
# space-separated form alone would silently miss them). PREFIXES matches
# "{marker} {model}" (space) OR "{marker}{model}" (fused) the same way --
# needed because a prefix-style distinct product (e.g. Toyota "Proace Verso"
# contaminating a "Verso" pool, confirmed 2026-08-17) is not expressible as a
# suffix pattern. Deliberately a short, evidenced list (largely from
# audit_model_line_contamination.py's 2026-08-28 sweep of every Autobazar
# (brand, model) pair against every other), not a general model-line
# database -- every other (brand, model) pair keeps the prior soft behavior
# unchanged.
_DISTINCT_MODEL_LINE_SUFFIXES = {
    ("volkswagen", "passat"): ("cc", "alltrack"),
    ("volkswagen", "golf"): ("sportsvan",),
    # Ioniq 5 / Ioniq 6 are Hyundai's dedicated E-GMP electric platform (2021+
    # / 2022+), a completely different vehicle from the original Ioniq
    # hatchback (2016-2022, ICE/hybrid/PHEV/BEV) -- confirmed live 2026-08-28:
    # a "Hyundai Ioniq 5" listing (fuel unknown, not rejected) passed BROAD
    # against a submitted Ioniq PHEV purely because "Ioniq" is a substring of
    # "Ioniq 5".
    ("hyundai", "ioniq"): ("5", "6"),
    # Mach-E is Ford's fully-electric SUV -- shares the Mustang nameplate for
    # marketing only, completely different platform/powertrain/body from the
    # combustion coupe.
    ("ford", "mustang"): ("mach-e",),
    # C4 Cactus was marketed and priced as its own distinct nameplate (quirky
    # SUV-styled hatchback), not a C4 trim/body-style.
    ("citroen", "c4"): ("cactus",),
    # CL (large luxury coupe) vs CLA/CLE/CLS (compact coupe/sedan, coupe
    # replacing C/E-Class coupes, 4-door coupe respectively) -- three
    # genuinely separate Mercedes model lines, all fused (no space) onto the
    # bare "CL" substring.
    ("mercedes-benz", "cl"): ("a", "e", "s"),
}
_DISTINCT_MODEL_LINE_PREFIXES = {
    ("toyota", "verso"): ("proace", "corolla"),
    # XCeed is a genuinely distinct crossover; "X" is fused onto "Ceed" as a
    # PREFIX with no space ("XCeed"), not a suffix.
    ("kia", "ceed"): ("x",),
}


def _model_line_excluded(alias: str, model: str, t: str) -> bool:
    """True if `t` (a lowercased title) matches a distinct-model-line
    exclusion for (alias, model) -- spaced OR fused form, see the tables'
    own docstring above."""
    for suffix in _DISTINCT_MODEL_LINE_SUFFIXES.get((alias, model), ()):
        if f"{model} {suffix}" in t or f"{model}{suffix}" in t:
            return True
    for prefix in _DISTINCT_MODEL_LINE_PREFIXES.get((alias, model), ()):
        if f"{prefix} {model}" in t or f"{prefix}{model}" in t:
            return True
    return False


def rule_model_soft(car: InvCar, row) -> str:
    title = _clean(row["title"])
    if not title:
        return UNKNOWN
    t = str(title).lower()
    model = car.model.lower()
    # Resolve through the SAME brand-alias set rule_brand/_title_has_brand
    # already use (e.g. "VW" and "Volkswagen" both need to reach the
    # "volkswagen" entry above) -- without this, a car whose inventory brand
    # is literally "VW" would never match the "volkswagen" key and the
    # exclusion would silently never fire for it.
    brand_aliases = _BRAND_ALIASES.get(car.brand.lower(), {car.brand.lower()})
    for alias in brand_aliases:
        if _model_line_excluded(alias, model, t):
            return MISMATCH

    # Locale-aware presence check: the model must be represented by the
    # searched model string itself OR one of its known locale/format variants
    # (reusing _model_slug_candidates, the same resolver already used for
    # Autobazar URL-slug matching -- e.g. BMW "3 Series" -> SK "Rad 3",
    # Mercedes "A-Class" -> SK "A trieda") -- so a genuine same-car listing in
    # a locale-renamed or differently-formatted title (a hyphen dropped, e.g.
    # "Cx-5" -> "CX5") is never rejected. Confirmed against the 2026-08-17
    # audit's full currently-matched population: zero false rejections.
    candidates = set(_model_slug_candidates(car.model))
    candidates.add(model)
    for cand in candidates:
        if cand in t or cand.replace(" ", "") in t or cand.replace("-", " ") in t:
            return MATCH
    # Title exists but the model isn't represented by any known form of it --
    # a different vehicle entirely (e.g. a Bazos free-text search for
    # "Volkswagen Touran" returning a "Golf Sportsvan" ad; confirmed during the
    # 2026-08-17 matching-quality audit). Previously this fell through as
    # UNKNOWN and was silently admitted; it is now a hard reject.
    return MISMATCH


# --------------------------------------------------------------------------- #
# Generation awareness (2026-08-28 business sign-off: "would you ever compare
# two cars from different generations? NO" -- a hard gate enforced at EVERY
# tier, same as brand/model). Deliberately a small, evidenced table of known
# generation year-boundaries for models actually seen in real inventory data,
# NOT a general database: a (brand, model) pair absent from this table never
# blocks a match (evidence over guessing -- missing a rare cross-generation
# contamination is far safer than falsely rejecting a real comparable for a
# model we have no boundary data for). Boundaries are approximate model-year
# splits (exact production-start dates vary a bit by market); extend/correct
# as needed. Open-ended on the high side (2035) so a currently-in-production
# generation keeps matching future model years without upkeep.
_GEN_BRAND_CANON = {
    "vw": "volkswagen", "volkswagen": "volkswagen",
    "mercedes": "mercedes-benz", "mercedes-benz": "mercedes-benz",
    "merc": "mercedes-benz", "benz": "mercedes-benz",
    "skoda": "skoda", "škoda": "skoda",
    "bmw": "bmw",
    "audi": "audi",
    "ford": "ford",
    "toyota": "toyota",
    "nissan": "nissan",
    "opel": "opel", "ope": "opel",  # 'Ope' is a real-world typo seen in dealer data
    "kia": "kia",
    "mazda": "mazda",
    "hyundai": "hyundai",
    "seat": "seat",
}

_GENERATION_BOUNDARIES: dict[tuple[str, str], list[tuple[int, int, str]]] = {
    ("skoda", "octavia"): [(2004, 2012, "2"), (2013, 2019, "3"), (2020, 2035, "4")],
    ("skoda", "superb"): [(2008, 2014, "2"), (2015, 2022, "3"), (2023, 2035, "4")],
    ("skoda", "fabia"): [(2007, 2013, "2"), (2014, 2020, "3"), (2021, 2035, "4")],
    ("skoda", "kodiaq"): [(2016, 2022, "1"), (2023, 2035, "2")],
    ("skoda", "karoq"): [(2017, 2035, "1")],
    ("volkswagen", "golf"): [(2008, 2011, "6"), (2012, 2018, "7"), (2019, 2035, "8")],
    ("volkswagen", "passat"): [(2010, 2013, "b7"), (2014, 2022, "b8"), (2023, 2035, "b9")],
    ("volkswagen", "tiguan"): [(2007, 2015, "1"), (2016, 2023, "2"), (2024, 2035, "3")],
    ("volkswagen", "touran"): [(2003, 2009, "1"), (2010, 2014, "2"), (2015, 2035, "3")],
    ("volkswagen", "polo"): [(2009, 2016, "5"), (2017, 2035, "6")],
    ("volkswagen", "t-cross"): [(2018, 2035, "1")],
    ("bmw", "3 series"): [(2011, 2018, "f30"), (2019, 2035, "g20")],
    ("bmw", "5 series"): [(2009, 2016, "f10"), (2017, 2022, "g30"), (2023, 2035, "g60")],
    ("audi", "a3"): [(2012, 2019, "8v"), (2020, 2035, "8y")],
    ("audi", "a4"): [(2007, 2014, "b8"), (2015, 2035, "b9")],
    ("audi", "q5"): [(2008, 2016, "8r"), (2017, 2035, "fy")],
    ("mercedes-benz", "c-class"): [(2007, 2013, "w204"), (2014, 2020, "w205"), (2021, 2035, "w206")],
    ("ford", "focus"): [(2010, 2017, "3"), (2018, 2035, "4")],
    ("ford", "fiesta"): [(2008, 2016, "7"), (2017, 2035, "8")],
    ("toyota", "yaris"): [(2011, 2019, "3"), (2020, 2035, "4")],
    ("toyota", "corolla"): [(2013, 2018, "e17x"), (2019, 2035, "e210")],
    ("nissan", "qashqai"): [(2007, 2013, "j10"), (2014, 2020, "j11"), (2021, 2035, "j12")],
    ("opel", "astra"): [(2015, 2020, "k"), (2021, 2035, "l")],
    ("opel", "grandland x"): [(2017, 2023, "1"), (2024, 2035, "2")],
    ("kia", "ceed"): [(2012, 2017, "jd"), (2018, 2035, "cd")],
    ("mazda", "cx-5"): [(2012, 2016, "ke"), (2017, 2035, "kf")],
    ("hyundai", "tucson"): [(2015, 2019, "tl"), (2020, 2035, "nx4")],
    ("seat", "ibiza"): [(2008, 2016, "4"), (2017, 2035, "5")],
    ("seat", "leon"): [(2012, 2019, "3"), (2020, 2035, "4")],
}


def _generation_for(brand: Optional[str], model: Optional[str], year: Optional[int]) -> Optional[str]:
    if year is None:
        return None
    brand_key = _GEN_BRAND_CANON.get((brand or "").strip().lower())
    if brand_key is None:
        return None
    ranges = _GENERATION_BOUNDARIES.get((brand_key, (model or "").strip().lower()))
    if not ranges:
        return None
    for lo, hi, label in ranges:
        if lo <= year <= hi:
            return label
    return None  # year falls outside every known range for this model -> no assertion


def rule_generation(car: InvCar, row) -> str:
    """Hard-blocks a cross-generation match. Only fires when we have boundary
    data for this (brand, model) AND both years resolve to a known generation
    -- otherwise NA/UNKNOWN, never a false reject for an uncovered model."""
    if car.year is None:
        return NA
    car_gen = _generation_for(car.brand, car.model, car.year)
    if car_gen is None:
        return NA
    row_year = _int(row["year"])
    if row_year is None:
        return UNKNOWN
    row_gen = _generation_for(car.brand, car.model, row_year)
    if row_gen is None:
        return UNKNOWN
    return MATCH if row_gen == car_gen else MISMATCH


def rule_fuel(car: InvCar, row, require_known: bool = False) -> str:
    """`require_known` (2026-08-30, same pattern as rule_variant/rule_
    transmission/rule_year): fuel has no require_same_* on/off toggle --
    it's always checked when car.fuel is known, at every tier including
    BROAD -- but unlike km/power, an unknown LISTING fuel was passing
    STRICT/MODERATE unchallenged via "unknown never fails". STRICT/
    MODERATE now require it to be known; BROAD is untouched (require_known
    left False there)."""
    if not car.fuel:
        return NA
    val = _clean(row["fuel"])
    if val is None:
        return MISMATCH if require_known else UNKNOWN
    return MATCH if str(val).lower() == car.fuel.lower() else MISMATCH


def rule_variant(car: InvCar, row, require: bool, require_known: bool = False) -> str:
    """`require_known` (2026-08-30, mirrors rule_year's require_known_year):
    STRICT/MODERATE claim to enforce "same engine/variant", but without this
    an UNKNOWN listing variant passed through via "unknown never fails" the
    same as everywhere else -- a bare-title listing with no engine info at
    all (e.g. a Bazoš ad that's just "Škoda octavia", or ANY Autobazar
    listing today, since Autobazar rows never get a variant_engine value at
    all -- see bridge/to_display_fields.py's _load_autobazar_incremental)
    could land in STRICT/MODERATE despite the variant never actually being
    verified. See the 2026-08-30 live-recall audit that caught this (a
    manual-transmission Autobazar Octavia landing in strict/moderate)."""
    if not require or not car.variant_engine:
        return NA
    val = _clean(row["variant_engine"])
    if val is None:
        return MISMATCH if require_known else UNKNOWN
    return MATCH if str(val).upper() == car.variant_engine.upper() else MISMATCH


def rule_year(car: InvCar, row, tol: int, require_known: bool = False) -> str:
    if car.year is None:
        return NA
    val = _int(row["year"])
    if val is None:
        # Under STRICT we refuse to treat an unknown-year listing as a valid
        # +/-1-year comparable (it would otherwise pass via "unknown never fails"
        # and pull junk into the median, e.g. a year-less 'Toyota Rav4' shell).
        return MISMATCH if require_known else UNKNOWN
    return MATCH if abs(val - car.year) <= tol else MISMATCH


def rule_km(car: InvCar, row, pct: float, floor: int, require_known: bool = False) -> str:
    """`require_known` (2026-08-30, same pattern as rule_fuel above) -- a
    listing with no km extracted was passing STRICT/MODERATE's mileage
    check unchallenged via "unknown never fails". STRICT/MODERATE now
    require it to be known; BROAD is untouched."""
    if car.km is None:
        return NA
    val = _int(row["km"])
    if val is None:
        return MISMATCH if require_known else UNKNOWN
    window = max(int(car.km * pct), floor)
    return MATCH if abs(val - car.km) <= window else MISMATCH


def rule_power(car: InvCar, row, tol: Optional[int]) -> str:
    if tol is None or car.power_kw is None:
        return NA
    val = _int(row["power_kw"])
    if val is None:
        return UNKNOWN
    return MATCH if abs(val - car.power_kw) <= tol else MISMATCH


def rule_transmission(car: InvCar, row, require: bool, require_known: bool = False) -> str:
    """`require_known` (2026-08-30, mirrors rule_variant's own param above) --
    same "unknown never fails" gap: a listing with no transmission extracted
    at all was passing STRICT/MODERATE's "transmission must match" check
    unchallenged. Business rule (2026-08-30): a KNOWN-different transmission
    can only ever be a BROAD match, never moderate or strict -- an unknown
    transmission that turns out to actually differ must not silently land
    in strict/moderate either, so STRICT/MODERATE require the value to be
    known, not merely "match if known"."""
    if not require or not car.transmission:
        return NA
    val = _clean(row["transmission"])
    if val is None:
        return MISMATCH if require_known else UNKNOWN
    return MATCH if str(val).lower() == car.transmission.lower() else MISMATCH


def rule_drivetrain(car: InvCar, row, require: bool) -> str:
    """
    Only blocks when BOTH sides have a POSITIVELY detected drivetrain (never
    inferred from absence -- see normalize_drivetrain's own docstring).

    Reads from `row["title"]` rather than a precomputed "drivetrain" column:
    MarketCollectorProvider's bridge (the current default market data source,
    a SEPARATE sibling project) returns DataFrames with EXACTLY Carval's
    DISPLAY_FIELDS columns, which do not include "drivetrain" -- keying off
    "title" (which IS in DISPLAY_FIELDS) keeps this rule working identically
    across every provider without requiring a cross-repo schema change.
    """
    if not require or not car.drivetrain:
        return NA
    title = _clean(row["title"])
    val = normalize_drivetrain(title) if title else None
    if val is None:
        return UNKNOWN
    return MATCH if val.lower() == car.drivetrain.lower() else MISMATCH


# Canonical body-style classes. Keys are lowercased raw values observed in
# either the inventory or the collected listing data; values are the class
# they represent. Purely translation/formatting equivalents (Kombi/Combi are
# the Slovak/Czech/German word for the English "Estate"; "SUV / Off-road" and
# the concatenated-field artifact "Suvkaroséria" both mean plain SUV;
# "Limuzína" is the Slovak word for "Sedan"/"Limousine"; "Kabriolet" is the
# Slovak word for "Cabriolet" -- the Limuzína/Kabriolet pair confirmed during
# the 2026-08-18 verification pass, after a Limuzína-tagged comparable was
# found slipping through as UNKNOWN and, once it became a car's only
# remaining match, exposed an unprotected outlier price) map to the SAME
# class as their English form -- these are NOT treated as different body
# styles. Values with no automotive body-style meaning at all (e.g. "Biela" =
# Slovak for the colour "White"; "Má" = a stray description fragment -- both
# confirmed data-quality artifacts) are deliberately absent from this table,
# so they fall through to UNKNOWN rather than being forced into a body class
# or a false conflict. Compound/ambiguous labels ("Mpv/Van", "SUV /
# Hatchback", "SUV / Combi") are deliberately NOT included yet either --
# pending a separate audit -- so they also fall through to UNKNOWN.
_BODY_STYLE_CLASSES = {
    "estate": "estate", "kombi": "estate", "combi": "estate",
    "sports tourer": "estate", "variant": "estate", "touring": "estate",
    "suv": "suv", "suv / off-road": "suv", "off-road": "suv",
    "suvkaroséria": "suv", "crossover": "suv",
    "hatchback": "hatchback", "liftback": "liftback",
    "sedan": "sedan", "limousine": "sedan", "limuzína": "sedan",
    "coupe": "coupe", "coupé": "coupe",
    "mpv": "mpv", "van": "mpv",
    "cabrio": "cabrio", "cabriolet": "cabrio", "convertible": "cabrio",
    "kabriolet": "cabrio",
}


def _canon_body_style(value) -> Optional[str]:
    value = _clean(value)
    if value is None:
        return None
    return _BODY_STYLE_CLASSES.get(str(value).strip().lower())


def rule_body_type(car: InvCar, row) -> str:
    if not car.body_type:
        return NA
    car_class = _canon_body_style(car.body_type)
    if car_class is None:
        # Our own body_type is present but not a recognized class (shouldn't
        # normally happen post-norm_body, but never treat unrecognized data
        # as a rejection reason).
        return NA
    val = _clean(row["body_type"])
    if val is None:
        return UNKNOWN
    row_class = _canon_body_style(val)
    if row_class is None:
        # Garbage/unrecognized value on the comparable side (e.g. a colour or
        # a stray fragment) -- treated as unknown, never a mismatch.
        return UNKNOWN
    return MATCH if row_class == car_class else MISMATCH


def evaluate(car: InvCar, row, rules: RuleSet) -> tuple[bool, dict]:
    outcomes = {
        "brand": rule_brand(car, row),
        "model": rule_model_soft(car, row),
        "generation": rule_generation(car, row),
        "fuel": rule_fuel(car, row, rules.require_known_fuel),
        "variant": rule_variant(car, row, rules.require_same_variant, rules.require_known_variant),
        "year": rule_year(car, row, rules.year_tol, rules.require_known_year),
        "km": rule_km(car, row, rules.km_pct, rules.km_floor, rules.require_known_km),
        "power": rule_power(car, row, rules.power_tol_kw),
        "transmission": rule_transmission(car, row, rules.require_same_transmission, rules.require_known_transmission),
        "drivetrain": rule_drivetrain(car, row, rules.require_same_drivetrain),
        # body_type is informational only (2026-08-28 business sign-off: "it
        # does not matter as much... it can be even strict [i.e. never a
        # reject]"; model is the field that actually gates comparability) --
        # computed and reported for transparency but deliberately excluded
        # from the blocking set below.
        "body_type": rule_body_type(car, row),
    }
    passed = not any(
        v == MISMATCH for f, v in outcomes.items() if f != "body_type"
    )
    return passed, outcomes


# Slovak/Czech title markers of parts / for-scrap ads that still carry a km
# figure (so the price+attribute gate alone lets them through). e.g. a
# 'Predám kompletný motor ... 108.000km' engine listing.
_PARTS_TITLE_RE = re.compile(
    r"\b("
    r"na\s+diely|rozpred|"                 # selling for parts
    r"kompletn[yý]\s+motor|"                # complete engine
    r"n[aá]hradn[eé]\s+diel|diel[yov]|"     # spare parts
    r"prevodovk[au]|"                       # gearbox (as the item)
    r"dver[ei]|kapot|n[aá]raznik|blatnik|"  # doors / hood / bumper / fender
    r"sedačk|volant|"                       # seats / steering wheel
    r"pneumatik|disk[yov]|r[aá]fik"         # tyres / rims
    r")\b",
    re.I,
)

# Auction listings and lease-takeover offers (2026-08-28 business sign-off:
# both are a hard reject -- "auction is a reject. leasing takeover a reject.")
# The leasing pattern is confirmed against a real title seen in production
# scraping: "odstupim-leasing-na-hyundai-santa-fe-4x4..." (SK "odstúpim
# leasing" = "I'm giving up my lease").
_REJECT_TITLE_RE = re.compile(
    r"\b("
    r"aukci[aeu]|draž(?:ba|obn[yý])|auction|"                    # auction
    r"odst[uú]pim?\s+leasing|odst[uú]penie\s+od\s+leasingu|"     # leasing takeover
    r"prevod\s+leasingu|prevzatie\s+leasingu|postupenie\s+leasingu|"
    r"leasing\s+takeover"
    r")\b",
    re.I,
)


def qualifies_as_vehicle(row) -> bool:
    """
    Candidate-qualification gate applied BEFORE tolerance rules.

    Keyword search (and even category pages) surface spare-parts/for-scrap ads
    that carry the brand token in their title but are not whole cars. Those pass
    the tolerance rules because missing fields are treated as `unknown` (never
    fails) - and they wreck the median. A real listing must:
      * have a plausible price (>= VEHICLE_PRICE_FLOOR),
      * expose at least one vehicle attribute (year OR mileage), and
      * NOT be an obvious parts/for-scrap ad, auction, or lease-takeover offer
        by its title (2026-08-28 business sign-off: auction and leasing
        takeover are both a hard reject).
    This keeps even very cheap genuine cars (e.g. a 2006 Passat ~EUR 900) while
    dropping parts. It is deliberately NOT a tolerance rule, so the
    "unknown never fails" principle is preserved for real vehicles.
    """
    price = _int(row.get("price"))
    if price is None or price < VEHICLE_PRICE_FLOOR:
        return False
    title = row.get("title")
    if isinstance(title, str) and (_PARTS_TITLE_RE.search(title) or _REJECT_TITLE_RE.search(title)):
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
    for f in ["brand", "model", "generation", "fuel", "variant", "year", "km",
              "power", "transmission", "drivetrain", "body_type"]:
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


# --------------------------------------------------------------------------- #
# Mileage similarity (explicit confidence factor)
# --------------------------------------------------------------------------- #
# The comparability rules now gate km with a FLAT absolute band per tier
# (STRICT +/-20,000, MODERATE +/-40,000, BROAD +/-100,000 -- 2026-08-26
# change, widened further 2026-08-28, see phase5_compare.RuleSet's own
# comment; before the 08-26 change they were a PERCENTAGE
# of the submitted car's own mileage, which for a very high-mileage car ballooned
# the window enormously, e.g. a 250k km car once admitted +/-150k km under
# BROAD). This factor measures how well the ACTUAL comparable-mileage
# distribution matches the submitted car and downgrades confidence when it does
# not - WITHOUT touching the price calculation. It is a SEPARATE, softer signal
# than the hard match rules above, so it keeps its own independently-tuned
# relative-percentage thresholds below rather than reusing the (now flat, and
# therefore no longer expressible as a percentage) RuleSet km fields:
#   * MILEAGE_NOISE_FLOOR_KM = 20,000: absolute gaps this small are already
#     treated as negligible, so they are always GOOD. Still matches STRICT's
#     own flat km band by construction, just no longer derived from it.
#   * GOOD  <= 0.30  (comparable-median mileage within 30% of submitted)
#   * MODERATE <= 0.60 (still an accepted comparable, but loose)
#   * LARGE <= 1.00                     (median differs by up to 100%)
#   * VERY_LARGE > 1.00                 (median differs by more than 100%, e.g.
#                                        comparables have < half or > double the km)
# The relative distance uses the SMALLER of (submitted, comparable-median) as the
# base so the dangerous "high-mileage car vs low-mileage comparables" direction
# is not numerically compressed (250k vs 100k => 1.50, i.e. VERY_LARGE).
MILEAGE_NOISE_FLOOR_KM = 20_000
MILEAGE_GOOD_MAX = 0.30
MILEAGE_MODERATE_MAX = 0.60
MILEAGE_LARGE_MAX = 1.00

# Ordering for capping logic (higher = worse). "unknown" sits above moderate:
# we could not verify mileage, so it must not qualify for HIGH, but it is not as
# damning as a measured large gap.
MILEAGE_RANK = {"good": 0, "moderate": 1, "unknown": 1.5, "large": 2, "very_large": 3}


def mileage_similarity(car_km: Optional[int], comp_km) -> dict:
    """
    Compare the submitted car's mileage against the distribution of the
    comparable listings' mileage. Returns a transparent, self-describing dict:

        {
          "category": good|moderate|large|very_large|unknown,
          "comp_km_count": <#comparables with known km>,
          "comp_km_median", "comp_km_p25", "comp_km_p75": robust distribution,
          "submitted_km": <echo>,
          "rel_gap": <signed relative gap of the median vs submitted, or None>,
          "abs_gap_km": <|median - submitted|, or None>,
          "close_frac": <fraction of comparables within the STRICT band>,
          "direction": lower|higher|same|unknown  (comparables vs submitted),
          "note": <human sentence for the confidence reasons>,
        }

    `comp_km` may be a pandas Series/list of raw km values (NaNs/None ignored).
    """
    # Collect known comparable mileages.
    kms: list[int] = []
    if comp_km is not None:
        seq = comp_km.tolist() if isinstance(comp_km, pd.Series) else list(comp_km)
        for v in seq:
            iv = _int(v)
            if iv is not None and iv >= 0:
                kms.append(iv)

    base = {
        "category": "unknown",
        "comp_km_count": len(kms),
        "comp_km_median": None,
        "comp_km_p25": None,
        "comp_km_p75": None,
        "submitted_km": car_km,
        "rel_gap": None,
        "abs_gap_km": None,
        "close_frac": None,
        "direction": "unknown",
        "note": "",
    }

    if car_km is None:
        base["note"] = "submitted mileage unknown - could not assess mileage similarity"
        return base
    if not kms:
        base["note"] = "comparable mileage unavailable - could not verify mileage similarity"
        return base

    s = pd.Series(kms, dtype="float64")
    med = float(s.median())
    p25 = float(s.quantile(0.25))
    p75 = float(s.quantile(0.75))
    abs_gap = abs(med - car_km)

    # Fraction of comparables inside the engine's STRICT km band around the car.
    strict_window = max(int(car_km * MILEAGE_GOOD_MAX), MILEAGE_NOISE_FLOOR_KM)
    close_frac = float(((s - car_km).abs() <= strict_window).mean())

    direction = "same"
    if med < car_km:
        direction = "lower"   # comparables have LESS mileage -> value likely overstated
    elif med > car_km:
        direction = "higher"  # comparables have MORE mileage -> value likely understated

    # Category: absolute noise floor first, then relative distance off the
    # smaller base (amplifies the dangerous high-mileage direction).
    if abs_gap <= MILEAGE_NOISE_FLOOR_KM:
        category = "good"
        rel = abs_gap / max(min(med, car_km), 1)
    else:
        rel = abs_gap / max(min(med, car_km), 1)
        if rel <= MILEAGE_GOOD_MAX:
            category = "good"
        elif rel <= MILEAGE_MODERATE_MAX:
            category = "moderate"
        elif rel <= MILEAGE_LARGE_MAX:
            category = "large"
        else:
            category = "very_large"

    signed_rel = rel if direction == "higher" else (-rel if direction == "lower" else 0.0)

    base.update({
        "category": category,
        "comp_km_median": med,
        "comp_km_p25": p25,
        "comp_km_p75": p75,
        "rel_gap": round(signed_rel, 3),
        "abs_gap_km": int(abs_gap),
        "close_frac": round(close_frac, 2),
        "direction": direction,
        "note": _mileage_note(category, direction, med, car_km),
    })
    return base


def _mileage_note(category: str, direction: str, comp_median: float, car_km: int) -> str:
    """Human-readable confidence reason for a mileage category."""
    if category == "good":
        return "mileage closely matches comparable listings"
    dir_word = "lower" if direction == "lower" else "higher"
    med_s = f"{comp_median:,.0f} km"
    car_s = f"{car_km:,} km"
    if category == "moderate":
        return (f"comparable mileage is somewhat {dir_word} than the submitted car "
                f"(median {med_s} vs {car_s})")
    # large / very_large
    return (f"comparable mileage is substantially {dir_word} than the submitted car "
            f"(median {med_s} vs {car_s})")


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


# A used car's estimated market price should sit within a sane multiple of its
# asking price. Outside this band the "market median" is almost certainly a
# parse artifact (a monthly leasing rate, a deposit, a typo) rather than a real
# comparable - e.g. the VW Touran -229.7% case from a single bad Bazos listing.
_SANE_RATIO_LOW = 0.20   # market >= 20% of asking  (=> undervaluation <= +80%)
_SANE_RATIO_HIGH = 5.0   # market <= 5x asking       (=> overvaluation bounded)

# A MODERATE/BROAD group needs >= this many comparables to be trusted -- their
# looser tolerances (dropped variant/transmission requirements, wider mileage
# bands) mean any single match could easily be a poor stand-in for the actual
# car, so a bigger sample is required to average that noise out. Defined here
# (moved up from just above adaptive_estimate) so estimate() below can default
# to it.
MIN_USABLE = 4


def estimate(car: InvCar, strict: pd.DataFrame, min_usable: int = MIN_USABLE) -> dict:
    """`min_usable` is the sample-size floor below which the result is
    flagged "insufficient_sample". Defaults to MIN_USABLE (4) for any direct
    caller, but adaptive_estimate() below always passes 1 (2026-08-25): tier
    SELECTION already guarantees whatever pool is passed in here has >=1
    match unless literally nothing matched at any tier, so
    insufficient_sample effectively means "zero comparables", full stop --
    sample-size TRUST (vs. bare usability) is confidence_flag()'s job now,
    driven by which tier was reached, not a count floor here."""
    stats = price_stats(strict) if not strict.empty else {"count": 0}
    n = stats.get("count", 0)
    unk = _unknown_fraction(strict)
    warnings: list[str] = []
    # Mileage similarity of the chosen-tier comparables vs the submitted car.
    # Computed from the ACTUAL comparable km distribution; never merged across
    # sources (this runs per source). Does not affect any price figure.
    mileage = mileage_similarity(
        car.km, strict["km"] if not strict.empty and "km" in strict.columns else None
    )
    res = {
        "comparable_count": n,
        "outliers_trimmed": stats.get("outliers_trimmed", 0),
        "market_median": stats.get("median"),
        "market_p25": stats.get("p25"),
        "market_p75": stats.get("p75"),
        "estimated_market_price": stats.get("median"),
        "confidence": confidence_level(n, unk),
        "insufficient_sample": n < min_usable,
        "unknown_year_km_frac": round(unk, 2),
        # --- mileage similarity factor (transparent) ---
        "mileage_match": mileage["category"],
        "mileage": mileage,
    }
    if n and car.price:
        med = stats["median"]
        res["price_difference"] = med - car.price
        # "% below market" is measured against the ASKING price, not the market
        # median. Using the median as denominator made overpriced cars blow up to
        # nonsensical values (e.g. a EUR 12,200 Touran vs a lone EUR 3,700 ad read
        # -229.7% instead of the correct -69.7% = "priced ~70% above this sample").
        # With asking as the base: positive => below market (cheaper, a deal),
        # negative => above market, and the value is bounded at -100%+ sanely.
        res["undervaluation_pct"] = round((med - car.price) / car.price * 100, 1)

        # Single-listing estimates are inherently fragile: one mispriced ad IS
        # the median. Never let them read as reliable.
        if n == 1:
            warnings.append("single-listing estimate (n=1) - treat as indicative only")
            if res["confidence"] in ("medium", "high"):
                res["confidence"] = "low"

        # Implausible market/asking ratio => the median is not a real comparable.
        ratio = (med / car.price) if car.price else None
        if ratio is not None and (ratio < _SANE_RATIO_LOW or ratio > _SANE_RATIO_HIGH):
            warnings.append(
                f"implausible market/asking ratio {ratio:.2f} "
                f"(median EUR {med:,.0f} vs asking EUR {car.price:,.0f}); "
                "likely a price parse/outlier artifact - estimate suppressed"
            )
            # Suppress the misleading numeric signal but keep the raw stats for audit.
            res["estimated_market_price"] = None
            res["price_difference"] = None
            res["undervaluation_pct"] = None
            res["confidence"] = "none"
            res["insufficient_sample"] = True
    else:
        res["price_difference"] = None
        res["undervaluation_pct"] = None

    res["sample_warning"] = "; ".join(warnings) if warnings else ""
    return res


# Ordered tightest -> loosest. The adaptive selector reports the *tightest*
# tier with ANY match at all (2026-08-25: MIN_USABLE no longer gates
# SELECTION for any tier -- see adaptive_estimate) -- so each car gets the
# most precise estimate its data can support instead of a one-size-fits-all
# count threshold. MIN_USABLE is now purely a confidence-labeling input
# (phase7_fullrun.confidence_flag): the number of comparables no longer
# decides whether a tier's data is usable, only how much to trust it.
TIERS = (STRICT, MODERATE, BROAD)


def adaptive_estimate(car: InvCar, source_df: pd.DataFrame) -> dict:
    """
    Evaluate a source's candidates at every tolerance tier and pick the
    TIGHTEST tier with at least one match, full stop -- STRICT, MODERATE, and
    BROAD are all treated the same way now (2026-08-25 change): a tier's
    tighter tolerances (variant/transmission for STRICT, mileage band width
    for all three) are what make a match meaningful, not how many of them
    there are. A source with 2 MODERATE matches now reports the MODERATE
    median, not a broader/less precise fallback pool it happens to have more
    of. (Earlier, MODERATE/BROAD still needed MIN_USABLE=4 to be selected at
    all, so a lone MODERATE match fell through to a BROAD-only fallback and
    got treated as though STRICT/MODERATE had nothing.)

    Sample-size trust is now confidence_flag()'s job, driven by which tier
    was reached (STRICT -> HIGH, MODERATE -> MEDIUM, BROAD -> LOW baseline),
    not this function's. Only a source with ZERO matches at every tier (the
    BROAD fallback below, matched empty) is genuinely insufficient.

    Returns the estimate plus provenance: `tier_used`, per-tier counts, and
    the matched rows for the chosen tier.
    """
    per_tier = {}
    chosen = None
    for rs in TIERS:
        matched, _ = match(car, source_df, rs)
        per_tier[rs.name] = len(matched)
        if chosen is None and len(matched) >= 1:
            chosen = (rs, matched)
    if chosen is None:
        rs = BROAD
        matched, _ = match(car, source_df, rs)
        chosen = (rs, matched)
    rs, matched = chosen
    # Any tier that got selected above did so with >=1 match already -- the
    # only way `matched` is empty here is the "nothing anywhere" fallback,
    # so min_usable=1 (i.e. insufficient_sample = n == 0) is correct for
    # every case, not just STRICT.
    est = estimate(car, matched, min_usable=1)
    est["tier_used"] = rs.name
    est["tier_counts"] = "/".join(f"{t.name[:3]}:{per_tier[t.name]}" for t in TIERS)
    return est, matched


def _row_identity_key(row) -> object:
    """Best available stable identity for a listing row, for de-duplicating the
    SAME physical listing across tier pools. listing_id/url are collection-time
    identifiers and are preferred; title is the last-resort fallback for rows
    missing both (matches the fallback already used by the tier-membership
    de-duplication logic validated in the 2026-08-17 simulation)."""
    return row.get("listing_id") or row.get("url") or row.get("title")


def retained_pool(car: InvCar, source_df: pd.DataFrame) -> dict:
    """
    Evaluate STRICT, MODERATE, and BROAD against one source's candidates and
    partition the result into three DISJOINT tier bands, instead of picking a
    single winning tier the way `adaptive_estimate` does.

    Because STRICT's tolerances are a strict subset of MODERATE's, which are a
    strict subset of BROAD's (and MODERATE/BROAD additionally drop STRICT's
    variant/transmission requirements rather than adding new ones), every
    listing STRICT admits is also admitted by MODERATE and BROAD, and every
    listing MODERATE admits is also admitted by BROAD -- i.e.
    STRICT ⊆ MODERATE ⊆ BROAD as matched sets. Naively concatenating the three
    matched DataFrames would therefore count a STRICT-level match up to three
    times. Partitioning by "the tightest tier that already admits this
    listing" is what makes each listing appear exactly once:

        strict        = matched by STRICT
        moderate_only = matched by MODERATE, not already in strict
        broad_only    = matched by BROAD, not already in moderate (or strict)

    This function does NOT decide how to weight or aggregate the three bands
    into a single price -- it only assembles and tags the retained pool so it
    can be inspected. `adaptive_estimate` (unchanged) remains the production
    estimator; this is an additional, parallel view over the same matching
    rules and tolerances.

    Returns:
        {
          "strict": DataFrame,        # STRICT-matched rows, "tier"="strict"
          "moderate_only": DataFrame, # MODERATE-only rows,  "tier"="moderate"
          "broad_only": DataFrame,    # BROAD-only rows,     "tier"="broad"
          "retained": DataFrame,      # the three concatenated (disjoint union)
          "n_strict": int, "n_moderate": int, "n_broad": int, "n_total": int,
        }
    """
    strict_matched, _ = match(car, source_df, STRICT)
    moderate_matched, _ = match(car, source_df, MODERATE)
    broad_matched, _ = match(car, source_df, BROAD)

    strict_keys = (set(strict_matched.apply(_row_identity_key, axis=1))
                   if not strict_matched.empty else set())
    moderate_only = (moderate_matched[~moderate_matched.apply(_row_identity_key, axis=1).isin(strict_keys)]
                      if not moderate_matched.empty else moderate_matched)
    moderate_keys = strict_keys | (set(moderate_only.apply(_row_identity_key, axis=1))
                                    if not moderate_only.empty else set())
    broad_only = (broad_matched[~broad_matched.apply(_row_identity_key, axis=1).isin(moderate_keys)]
                  if not broad_matched.empty else broad_matched)

    strict_tagged = strict_matched.copy()
    if not strict_tagged.empty:
        strict_tagged["tier"] = "strict"
    moderate_tagged = moderate_only.copy()
    if not moderate_tagged.empty:
        moderate_tagged["tier"] = "moderate"
    broad_tagged = broad_only.copy()
    if not broad_tagged.empty:
        broad_tagged["tier"] = "broad"

    bands = [b for b in (strict_tagged, moderate_tagged, broad_tagged) if not b.empty]
    retained = pd.concat(bands, ignore_index=True) if bands else source_df.iloc[0:0].copy()

    return {
        "strict": strict_tagged,
        "moderate_only": moderate_tagged,
        "broad_only": broad_tagged,
        "retained": retained,
        "n_strict": len(strict_tagged),
        "n_moderate": len(moderate_tagged),
        "n_broad": len(broad_tagged),
        "n_total": len(strict_tagged) + len(moderate_tagged) + len(broad_tagged),
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def fmt(v) -> str:
    return f"EUR {v:,.0f}" if isinstance(v, (int, float)) and v is not None else "n/a"


def top_links(df: pd.DataFrame, k: int = 3) -> list[str]:
    # Defensive: a source that returns zero listings yields an empty, columnless
    # frame. Return no links rather than raising a KeyError on the missing
    # 'url' column (this is the common "no comparables" / INSUFFICIENT case).
    if df is None or df.empty or "url" not in df.columns:
        return []
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
        print(f"\nSTRICT  : {STRICT.describe()}")
        print(f"MODERATE: {MODERATE.describe()}")
        print(f"BROAD   : {BROAD.describe()}")
        print(f"\nAdaptive tier: report the TIGHTEST tier reaching n>={MIN_USABLE} comparables; "
              "else fall back to BROAD (flagged insufficient).")
        print("\nNOTE: prices assumed EUR on BOTH sides (inventory currency UNCONFIRMED).")
    print("Retrieval: Autobazar CATEGORY PATH (keyword fallback on 404) + Bazos keyword, "
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

        # Adaptive tier selection per source (tightest tier reaching MIN_USABLE).
        _, disq_ab = match(car, ab_df, BROAD)   # broad superset -> parts count
        _, disq_bz = match(car, bz_df, BROAD)
        n_parts = disq_ab + disq_bz
        if n_parts:
            print(f"  filtered {n_parts} non-vehicle/parts listing(s) before matching")

        ab_est, ab_matched = adaptive_estimate(car, ab_df)
        bz_est, bz_matched = adaptive_estimate(car, bz_df)

        # Persist the chosen-tier comparables for auditability.
        for src, est, matched in (("autobazar", ab_est, ab_matched),
                                  ("bazos", bz_est, bz_matched)):
            if not matched.empty:
                matched.to_csv(
                    f"{out_dir}/car{car.row_index}_{src}_{est['tier_used']}.csv",
                    index=False,
                )

        for src, est in (("AUTOBAZAR", ab_est), ("BAZOS", bz_est)):
            print(f"  -- {src} -- tier={est['tier_used'].upper()} "
                  f"[{est['tier_counts']}] | "
                  f"comparables={est['comparable_count']} | "
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

        for src, est, matched in (("autobazar", ab_est, ab_matched),
                                  ("bazos", bz_est, bz_matched)):
            summary_rows.append({
                "row_index": car.row_index,
                "brand": car.brand, "model": car.model, "fuel": car.fuel,
                "year": car.year, "km": car.km, "power_kw": car.power_kw,
                "inventory_price_eur": car.price,
                "source": src,
                **est,
                "cross_source_note": agree,
                "example_links": " | ".join(top_links(matched)),
                "retrieval_error": (ab_err if src == "autobazar" else bz_err) or "",
            })

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(f"{out_dir}/phase6_summary.csv", index=False)
    print("\n" + "=" * 78)
    print(f"Saved per-car group CSVs and summary -> {out_dir}/phase6_summary.csv")
    print("\n=== SUMMARY (adaptive tier per source) ===")
    show = summary[["row_index", "brand", "model", "fuel", "source",
                    "tier_used", "comparable_count", "estimated_market_price",
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
