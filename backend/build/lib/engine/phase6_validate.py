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


def _fetch_ab_pages(url_for_page, max_pages: int, delay: float) -> list[dict]:
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        html = ab.fetch(url_for_page(page))  # raises BlockedError -> caller handles
        listings, _mode = ab.parse_listings(html)
        if not listings:
            break
        rows.extend(_norm_market_row("autobazar", r) for r in listings)
        if page < max_pages:
            time.sleep(delay)
    return rows


def retrieve_autobazar(car: InvCar, max_pages: int, delay: float) -> tuple[pd.DataFrame, Optional[str]]:
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
    try:
        rows = _fetch_ab_pages(cat_url, max_pages, delay)
    except ab.BlockedError as e:
        # 404 = wrong slug -> keyword fallback; anything else is a real stop.
        if "HTTP 404" not in str(e):
            return pd.DataFrame(), f"BLOCKED: {e}"
        note = f"category slug {brand_slug}/{model_slug} 404 -> keyword fallback"
        time.sleep(delay)
        keyword = f"{car.brand} {car.model}"
        try:
            rows = _fetch_ab_pages(
                lambda p: ab.build_search_url(keyword=keyword, page=p), max_pages, delay
            )
        except ab.BlockedError as e2:
            return pd.DataFrame(), f"BLOCKED (fallback): {e2}"
        except Exception as e2:  # noqa: BLE001
            return pd.DataFrame(), f"ERROR (fallback): {type(e2).__name__}: {e2}"
    except Exception as e:  # noqa: BLE001 - surface, never hide
        return pd.DataFrame(), f"ERROR: {type(e).__name__}: {e}"

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset="url").reset_index(drop=True)
        return df, note
    # Fetched OK but empty -> genuine no-data (record the slug/mode we tried), NOT
    # a scraper failure. Keeps any existing fallback note appended.
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


def retrieve_bazos(car: InvCar, max_pages: int, delay: float) -> tuple[pd.DataFrame, Optional[str]]:
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
    try:
        for page in range(1, max_pages + 1):
            url = bz.build_search_url(query, crp=bz.crp_for_page(page))
            html = bz.fetch(url)
            pages_ok += 1
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
    # Fetched fine but nothing came back -> genuine no-data, not a failure.
    return df, f"no listings for query '{query}' (fetched {pages_ok} page(s) OK)"


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
        "year": rule_year(car, row, rules.year_tol, rules.require_known_year),
        "km": rule_km(car, row, rules.km_pct, rules.km_floor),
        "power": rule_power(car, row, rules.power_tol_kw),
        "transmission": rule_transmission(car, row, rules.require_same_transmission),
    }
    passed = not any(v == MISMATCH for v in outcomes.values())
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


def qualifies_as_vehicle(row) -> bool:
    """
    Candidate-qualification gate applied BEFORE tolerance rules.

    Keyword search (and even category pages) surface spare-parts/for-scrap ads
    that carry the brand token in their title but are not whole cars. Those pass
    the tolerance rules because missing fields are treated as `unknown` (never
    fails) - and they wreck the median. A real listing must:
      * have a plausible price (>= VEHICLE_PRICE_FLOOR),
      * expose at least one vehicle attribute (year OR mileage), and
      * NOT be an obvious parts/for-scrap ad by its title.
    This keeps even very cheap genuine cars (e.g. a 2006 Passat ~EUR 900) while
    dropping parts. It is deliberately NOT a tolerance rule, so the
    "unknown never fails" principle is preserved for real vehicles.
    """
    price = _int(row.get("price"))
    if price is None or price < VEHICLE_PRICE_FLOOR:
        return False
    title = row.get("title")
    if isinstance(title, str) and _PARTS_TITLE_RE.search(title):
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


# A used car's estimated market price should sit within a sane multiple of its
# asking price. Outside this band the "market median" is almost certainly a
# parse artifact (a monthly leasing rate, a deposit, a typo) rather than a real
# comparable - e.g. the VW Touran -229.7% case from a single bad Bazos listing.
_SANE_RATIO_LOW = 0.20   # market >= 20% of asking  (=> undervaluation <= +80%)
_SANE_RATIO_HIGH = 5.0   # market <= 5x asking       (=> overvaluation bounded)


def estimate(car: InvCar, strict: pd.DataFrame) -> dict:
    stats = price_stats(strict) if not strict.empty else {"count": 0}
    n = stats.get("count", 0)
    unk = _unknown_fraction(strict)
    warnings: list[str] = []
    res = {
        "comparable_count": n,
        "outliers_trimmed": stats.get("outliers_trimmed", 0),
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


MIN_USABLE = 4  # a group needs >= this many comparables to be trusted

# Ordered tightest -> loosest. The adaptive selector reports the *tightest* tier
# that reaches MIN_USABLE, so each car gets the most precise estimate its data
# can support instead of a one-size-fits-all threshold.
TIERS = (STRICT, MODERATE, BROAD)


def adaptive_estimate(car: InvCar, source_df: pd.DataFrame) -> dict:
    """
    Evaluate a source's candidates at every tolerance tier and pick the tightest
    tier reaching MIN_USABLE comparables. If none do, fall back to the loosest
    (BROAD) and flag it insufficient. Returns the estimate plus provenance:
    `tier_used`, per-tier counts, and the matched rows for the chosen tier.
    """
    per_tier = {}
    chosen = None
    for rs in TIERS:
        matched, _ = match(car, source_df, rs)
        per_tier[rs.name] = len(matched)
        if chosen is None and len(matched) >= MIN_USABLE:
            chosen = (rs, matched)
    if chosen is None:
        rs = BROAD
        matched, _ = match(car, source_df, rs)
        chosen = (rs, matched)
    rs, matched = chosen
    est = estimate(car, matched)
    est["tier_used"] = rs.name
    est["tier_counts"] = "/".join(f"{t.name[:3]}:{per_tier[t.name]}" for t in TIERS)
    return est, matched


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
