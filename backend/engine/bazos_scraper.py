"""
Phase 4: deterministic Bazos.sk (auto.bazos.sk) retrieval + parser.

Goal
----
Build a reusable Bazos ingestion layer that produces the SAME normalized schema
as the Autobazar layer, so a later normalization/comparability stage can consume
both uniformly. This module is intentionally rule-based (regex/keyword) — there
is NO LLM, NO database, NO frontend, and NO comparability logic here.

Why Bazos is harder than Autobazar
----------------------------------
Autobazar.eu is a Next.js app that embeds clean typed JSON. Bazos.sk is classic
server-rendered HTML where only a few fields are structured:

    - title            <h2 class="nadpis"><a href="/inzerat/{id}/{slug}.php">
    - price            <div class="inzeratycena">  e.g. "26 800 €"
    - listing_date     <span class="velikost10"> ... [11.8. 2026]
    - image            <img class="obrazek">

Everything else (year, mileage, fuel, engine/variant, power, transmission,
body type) lives in FREE-FORM Slovak prose in <div class="popis"> and/or the
title. We extract those with small, independent, individually testable regex
functions that return None on no-match and NEVER guess.

Provenance
----------
Every vehicle-spec field records HOW it was obtained via a parallel
`{field}_source` value: one of "structured", "regex_description", "regex_title",
or "missing". This lets the later matcher trust Autobazar structured data more
than uncertain Bazos extraction.

Anti-bot policy
---------------
Public pages only. If the site returns 401/403/429/5xx or shows a challenge, we
STOP (BlockedError) and report — we never bypass CAPTCHA / Cloudflare / rate
limits / fingerprinting / proxies.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

try:
    import pandas as pd
except ImportError:
    pd = None

# Reuse the vetted primitives from the Autobazar layer (same folder / package).
from autobazar_scraper import BlockedError, HEADERS, NotFoundError, clean_int  # noqa: E402
import http_client  # bounded, instrumented GET (hard wall-clock deadline)
from http_client import FetchTimeout  # noqa: F401 - re-exported for callers


BASE = "https://auto.bazos.sk"
RESULTS_PER_PAGE = 20  # Bazos returns 20 per page; `crp` is the row offset.

# Provenance sentinels.
SRC_STRUCTURED = "structured"
SRC_DESC = "regex_description"
SRC_TITLE = "regex_title"
SRC_MISSING = "missing"

# Normalized schema shared with Autobazar, plus Bazos-specific audit columns.
SPEC_FIELDS = [
    "year", "km", "fuel", "transmission", "engine", "power", "body_type",
]
OUTPUT_COLUMNS = [
    "source",
    "listing_id",
    "title",
    "year",
    "km",
    "price",
    "fuel",
    "transmission",
    "engine",
    "power",
    "body_type",
    "listing_date",
    "url",
    "search_query",
    "search_page",
    # audit / provenance
    "raw_description",
    "filter_reason",
] + [f"{f}_source" for f in SPEC_FIELDS]


# --------------------------------------------------------------------------- #
# URL construction + pagination
# --------------------------------------------------------------------------- #
def build_search_url(query: str, crp: int = 0, radius_km: int = 25) -> str:
    """Build a public Bazos auto search URL.

    The homepage GET form submits to the site root with these params:
      hledat   -> free-text query
      humkreis -> search radius in km (paired with hlokalita postcode; blank = SK-wide)
      cenaod / cenado -> price from/to (left blank here)
      crp      -> result row offset for pagination (0, 20, 40, ...)
    """
    params = {
        "hledat": query,
        "hlokalita": "",
        "humkreis": str(radius_km),
        "cenaod": "",
        "cenado": "",
    }
    if crp:
        params["crp"] = str(crp)
    return f"{BASE}/?{urlencode(params)}"


def crp_for_page(page: int) -> int:
    """page 1 -> crp 0, page 2 -> crp 20, page 3 -> crp 40, ..."""
    return (page - 1) * RESULTS_PER_PAGE


# --------------------------------------------------------------------------- #
# Fetching (stop-and-report on any block; never bypass)
# --------------------------------------------------------------------------- #
def fetch(url: str, timeout: int = 30, *, brand: str = "", model: str = "",
          page: int = 0, deadline: float | None = None) -> str:
    """GET one page with ordinary HTTP; raise BlockedError on any block signal.

    The GET is hard-bounded by http_client (wall-clock deadline, capped
    redirects/size, no retries), so a slow/hanging server can no longer block
    the run. A timeout surfaces as FetchTimeout (a SOFT, non-block failure) and
    propagates to the caller, which keeps any partial results and moves on. A
    network/connection error still maps to BlockedError, as before.

    `deadline` (absolute time.perf_counter() value, optional) is the per-model
    time budget: when given, this request's ceilings are shrunk to the remaining
    time so it cannot overshoot the budget, and if no time is left we raise
    FetchTimeout immediately without touching the network.
    """
    bounds: dict = {}
    if deadline is not None:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise FetchTimeout(f"{url!r}: model time budget exhausted before request")
        bounds = http_client.bounds_for_remaining(remaining)

    try:
        status, body, _headers = http_client.get_bounded(
            url, HEADERS, source="bazos", brand=brand, model=model, page=page,
            **bounds,
        )
    except FetchTimeout:
        raise  # non-block soft failure; do NOT convert to BlockedError
    except requests.RequestException as exc:
        raise BlockedError(f"Network error retrieving {url!r}: {exc}") from exc

    body = body or ""

    if status in (401, 403, 429) or status >= 500:
        raise BlockedError(
            f"Request to {url!r} returned HTTP {status}. "
            f"Stopping without any bypass attempt (possible rate-limit/restriction)."
        )

    lowered = body[:5000].lower()
    challenge_markers = (
        "cf-challenge", "cf-turnstile", "just a moment", "captcha",
        "attention required", "access denied",
        "please enable javascript and cookies",
    )
    hit = next((m for m in challenge_markers if m in lowered), None)
    if hit:
        raise BlockedError(
            f"Anti-bot challenge detected (marker: {hit!r}) at {url!r}. "
            f"Stopping — no CAPTCHA/Cloudflare bypass will be attempted."
        )

    # Definitive "gone" -- distinct from a block, and from the generic
    # fallback below: a removed/expired listing, not a failure to retry.
    if status in (404, 410):
        raise NotFoundError(f"{url!r} returned HTTP {status} (listing not found/gone).")

    if status != 200:
        raise BlockedError(f"Unexpected HTTP {status} for {url!r}.")

    return body


# --------------------------------------------------------------------------- #
# Field extractors — small, independent, individually testable.
# Each returns a normalized value or None. None => not confidently detected.
# --------------------------------------------------------------------------- #

# ---- Year ---------------------------------------------------------------- #
# Ordered, labelled patterns. We deliberately anchor on a label/keyword so we do
# NOT mistake unrelated numbers (mileage, price, engine cc) for the year.
_YEAR_LABEL = r"(?:modelov[yý]\s*rok|rok\s*v[yý]roby|1\.?\s*prihl[aá]senie|rok|r\.?v\.?|rv)"
_YEAR_PATTERNS = [
    # label: 05/2021  |  label: 20.5.2022  |  label: 2021
    # allow up to two leading DD.MM. (or MM.) segments before the 4-digit year
    re.compile(_YEAR_LABEL + r"\s*[:\-]?\s*(?:\d{1,2}[./]\s*){0,2}((?:19|20)\d{2})", re.I),
    # rv:05/2022  /  rv 1/2022 (compact, common in titles)
    re.compile(r"\br\.?\s*v\.?\s*[:\-]?\s*(?:\d{1,2}[./])?((?:19|20)\d{2})", re.I),
    # Last-resort UNLABELLED date-like forms only (never a bare 4-digit number):
    #   dotted full date '20.5.2022'
    re.compile(r"\b\d{1,2}\.\d{1,2}\.\s*((?:19|20)\d{2})\b"),
    #   month/year '1/2022' or '05/2022' (kept last so labelled years win)
    re.compile(r"\b\d{1,2}/((?:19|20)\d{2})\b"),
]
# Indices into _YEAR_PATTERNS above that are "last-resort UNLABELLED" (no
# year-ish keyword required at all) -- the only ones at risk of misreading an
# unrelated future validity/expiry date as the car's own year. Both share
# that risk category, not just the last one.
_UNLABELLED_PATTERN_INDICES = {2, 3}
# Confirmed live 2026-08-28: "STK/EK do 12/2026" (Slovak: technical/emissions
# inspection valid until) matched the bare month/year pattern and silently
# won over the CORRECT year sitting right there in the title ("Seat Ibiza fr
# 2016" extracted as 2026) -- 734 rows affected in the captured dataset.
# Insurance ("poistka"/"poistenie") and warranty ("záruka") are the same
# shape of problem and included pre-emptively, not yet separately confirmed
# at the same scale.
_YEAR_FALSE_CONTEXT_RE = re.compile(
    r"\bstk\b|\bek\b|\btk\b|kontrol|poist|z[aá]ruk", re.I,
)
_YEAR_CONTEXT_WINDOW = 20  # chars immediately before the match to inspect


def extract_year(text: str) -> int | None:
    """Extract a plausible model/production year (1990-current+1).

    Only matches when tied to a year-ish label, so it will NOT read the '137782'
    in 'Najazdené: 137 782 km' (no label, and not a 4-digit 19xx/20xx anyway).
    """
    if not text:
        return None
    import datetime
    max_year = datetime.date.today().year + 1
    for pi, pat in enumerate(_YEAR_PATTERNS):
        for m in pat.finditer(text):
            y = int(m.group(1))
            if not (1990 <= y <= max_year):
                continue
            if pi in _UNLABELLED_PATTERN_INDICES:
                context = text[max(0, m.start() - _YEAR_CONTEXT_WINDOW):m.start()]
                if _YEAR_FALSE_CONTEXT_RE.search(context):
                    continue
            return y
    return None


# ---- Mileage ------------------------------------------------------------- #
# Thousands separators seen in the wild: space, non-breaking space, and a DOT
# ('100.500 km' == 100500). We keep the dot inside the captured number and let
# clean_int() strip it; this fixes both a dropped-value bug ('100.500' -> 500)
# and several misses ('109.000KM').
_KM_NUM = r"[\d][\d\s\u00a0.]{1,9}"
_KM_LABEL = r"(?:n[aá]jazd(?:en[eé])?(?:\s*(?:km|kilometre))?|kilometre|km stav|stav\s*km|tachometer|km)"
# Labelled: 'Nájazd: 110 000km', 'Najazdené km: 138 000', 'km: 159 999'
_KM_LABELLED = re.compile(_KM_LABEL + r"\s*[:\-]?\s*(" + _KM_NUM + r")\s*(?:km)?", re.I)
# Fallback: a number immediately followed by 'km' (e.g. '176 100 km', '37000km').
# The negative lookbehind for '/' stops us reading the '100' in 'L/100km'
# (fuel-consumption spec) as mileage.
_KM_UNIT = re.compile(r"(?<![/\d])(" + _KM_NUM + r")\s*km\b", re.I)
# Shorthand: '150tis km' / '150 tis. km' -> 150000
_KM_TIS = re.compile(r"(\d{1,3})\s*tis\.?\s*(?:km)?", re.I)


def extract_km(text: str) -> int | None:
    """Extract mileage in kilometres. Normalizes '137 782 km' -> 137782.

    Order: labelled value -> 'N tis km' shorthand -> bare 'N km'. Plausibility
    bounded to 1..1,000,000 so we don't accept a stray price/year as mileage.
    """
    if not text:
        return None

    def _plausible(v: int | None) -> int | None:
        return v if v is not None and 1 <= v <= 1_000_000 else None

    m = _KM_LABELLED.search(text)
    if m:
        v = _plausible(clean_int(m.group(1)))
        if v is not None:
            return v

    m = _KM_TIS.search(text)
    if m:
        v = _plausible(int(m.group(1)) * 1000)
        if v is not None:
            return v

    m = _KM_UNIT.search(text)
    if m:
        v = _plausible(clean_int(m.group(1)))
        if v is not None:
            return v

    return None


# ---- Fuel ---------------------------------------------------------------- #
# Order matters: check the more specific PHEV/hybrid/electric before 'benzin',
# and LPG/CNG before Petrol (a bi-fuel LPG car should report as LPG).
#
# Engine badges (TDI/TSI/...) are frequently glued to the displacement, e.g.
# '2.0TDI', so those badges use a letter-only boundary (?<![a-z])/(?![a-z]) that
# still allows a digit immediately before/after. The spelled-out fuel words keep
# strict \b word boundaries.
_B = r"(?<![a-z])"   # left: not preceded by a letter (digit/space/punct OK)
_Bx = r"(?![a-z])"   # right: not followed by a letter

# "elektrick[yýaeé]"/"elektro" is Slovak for "electric" as an EQUIPMENT
# adjective ("elektrické okná" = power windows, "elektricky ovládané ťažné
# zariadenie" = electrically-operated tow hitch) far more often in these ads
# than it describes the car's own propulsion -- confirmed 2026-08-28: this
# false-positive alone mislabeled ~8% of ALL captured Bazoš listings as
# "Electric" (6,682 rows, 91% of which have zero genuine electric signal in
# their own title -- e.g. a plain "2.0 TDI" Octavia whose description just
# happened to mention an electrically-operated tow hitch). This negative
# lookahead rejects exactly the equipment-noun contexts that caused it,
# without touching genuine propulsion mentions ("elektromobil", "elektrický
# pohon", "auto na elektrický pohon").
#
# 2026-08-29: widened from "the immediately next word" to "within the next
# 0-3 words" after a live recall audit found a real remaining miss --
# "Elektrické sklopné spätné zrkadlá" (electric fold-away rear mirrors) has
# TWO adjectives ("sklopné spätné") between the electric-adjective and the
# equipment noun ("zrkadlá"), which the single-word lookahead didn't reach.
_ELECTRIC_EQUIPMENT_NOUNS = (
    r"ovládan|okn|zrkadl|sedadl|vyhrievan|riaden|zamykan|"
    r"nastaviteľn|sklápa|st[eě]rač|ťažn|kľučk|kufr|strech"
)
_FUEL_RULES = [
    ("PHEV", re.compile(r"plug[\s\-]*in|phev", re.I)),
    ("Hybrid", re.compile(r"\bhybrid(?:n[yýaeé])?\b|\bhev\b", re.I)),
    ("Electric", re.compile(
        r"\belektr(?:o|ick[yýaeé]|omobil)\b"
        r"(?!\s*(?:\w+\s+){0,3}(?:" + _ELECTRIC_EQUIPMENT_NOUNS + r"))"
        r"|\bev\b|\bbev\b", re.I)),
    ("Diesel", re.compile(
        r"\bdiesel\b|\bnafta\b|" + _B + r"(?:tdi|cdi|hdi|dci|crdi|cdti)" + _Bx, re.I)),
    ("LPG", re.compile(r"\blpg\b", re.I)),
    ("CNG", re.compile(r"\bcng\b", re.I)),
    ("Petrol", re.compile(
        r"\bbenz[ií]n(?:ov[yýaeé])?\b|\bpetrol\b|" + _B + r"(?:tsi|tfsi|mpi|fsi)" + _Bx, re.I)),
]

# Structured "Palivo: <value>" label (2026-08-30) -- many Bazoš dealer-
# template ads state fuel explicitly, e.g. "▶️Palivo: Diesel". This is
# authoritative (the seller's own labeled field), unlike a keyword found
# ANYWHERE in a long free-text description -- and checking it FIRST fixes
# a real, still-recurring bug found via a live recall audit even after the
# 2026-08-28/29 equipment-noun exclusion fixes above: those fixes only
# closed specific noun-list gaps, but Slovak has many more inflected/
# derived equipment-adjective forms than any noun list can enumerate (this
# audit's own live example: "Predné elektricky nastaviteľné sedačky" uses
# "sedačky", not the listed "sedadl" stem; "elektricky ovládatelný kufor"
# uses the adjective "ovládateľný", not the listed "ovládan" noun stem --
# a genuine whack-a-mole problem). Worse, even a SHIELDED equipment mention
# elsewhere doesn't need to slip past the noun list at all to cause this:
# _FUEL_RULES checks "Electric" before "Diesel", so ANY unshielded
# "elektricky ..." equipment mention anywhere in the text outranks an
# explicit "Palivo: Diesel" label stated elsewhere in the SAME description
# -- confirmed live: listing 195016860, a plain "Škoda Octavia 2.0 TDI"
# whose own description says "▶️Palivo: Diesel", was stored as fuel=
# 'Electric' purely because of an unrelated "elektricky ovládatelný
# kufor" (electrically-operated boot) equipment mention later in the same
# text. Checking the label first sidesteps the whole equipment-noun
# problem for any ad that states it, rather than chasing an open-ended
# list of Slovak inflections.
_PALIVO_LABEL_RE = re.compile(r"\bpalivo\s*[:\-]?\s*([^\n▶✅✔•]{1,40})", re.I)
# 2026-08-30, second pass: a live recall audit after the Palivo fix above
# found the SAME "Electric" false-positive still recurring on ads that use
# a "Motor: <displacement> <badge/word>" field instead of (or in addition
# to) "Palivo:" -- e.g. "Motor: 1.5 TSI, benzín" or "Motor: 1,5 DIESEL (81
# kW)". Unlike Palivo, the fuel signal in a Motor value isn't always the
# first word and isn't always a spelled-out word at all (often just an
# engine badge like "dCi"/"TDI") -- confirmed live: listing 194923025, a
# "1.5 TSI, benzín" Kodiaq, was still stored as fuel='Electric' purely
# because of an unrelated "Elektrické otváranie/zatváranie kufra"
# (electric boot open/close) mention elsewhere in the same description --
# the "/" between "otváranie" and "zatváranie" even breaks the 0-3-word
# lookahead's own \w+\s+ tokenization, since there's no whitespace INSIDE
# that glued pair for it to count across. Rather than chase that
# tokenization edge case too, the Motor: label sidesteps it exactly the
# same way Palivo: does.
_MOTOR_LABEL_RE = re.compile(r"\bmotor\s*[:\-]?\s*([^\n▶✅✔•]{1,60})", re.I)
_FUEL_LABEL_KEYWORD_MAP = (
    ("plug", "PHEV"),
    ("hybrid", "Hybrid"),
    ("elekt", "Electric"),
    ("diesel", "Diesel"),
    ("nafta", "Diesel"),
    ("benz", "Petrol"),
    ("lpg", "LPG"),
    ("cng", "CNG"),
    # Engine badges -- only meaningful within an already-scoped Motor:
    # value (too broad to search for unscoped, which is exactly why
    # _FUEL_RULES below uses its own tighter, glued-to-digits boundary
    # version of these instead of reusing this map directly).
    ("tdi", "Diesel"), ("cdi", "Diesel"), ("hdi", "Diesel"),
    ("dci", "Diesel"), ("crdi", "Diesel"), ("cdti", "Diesel"),
    ("tsi", "Petrol"), ("tfsi", "Petrol"), ("mpi", "Petrol"), ("fsi", "Petrol"),
)
# A dealer sometimes states a COMPOUND value for a plug-in/mild hybrid,
# e.g. "Palivo: benzín + elektro/SOH 95,6%" -- neither half alone is the
# right answer (plain "Petrol" would silently discard the electric half
# entirely). Rather than guess PHEV vs. mild-hybrid from this alone,
# _fuel_value_to_label backs off and lets the ordinary free-text
# _FUEL_RULES below decide (which already correctly reads an explicit
# "Plug-in Hybrid"/"Hybrid" wording elsewhere in the title/description)
# -- this keeps the label check a pure improvement: it either fixes the
# equipment-noun false positive or no-ops back to prior behavior, never
# makes a compound case worse than it already was.
_LABEL_COMBUSTION_WORD = re.compile(r"diesel|nafta|benz", re.I)
_LABEL_ELECTRIC_WORD = re.compile(r"elekt", re.I)


def _fuel_value_to_label(value: str) -> str | None:
    if _LABEL_ELECTRIC_WORD.search(value) and _LABEL_COMBUSTION_WORD.search(value):
        return None  # compound value -- defer to the free-text rules below
    value = value.strip().lower()
    for keyword, label in _FUEL_LABEL_KEYWORD_MAP:
        if keyword in value:
            return label
    return None


def _fuel_from_label(text: str) -> str | None:
    # Palivo (an explicit "fuel" field) checked before Motor (an "engine
    # spec" field where fuel is secondary, incidental information) --
    # Palivo is the more directly authoritative source when both exist.
    for pat in (_PALIVO_LABEL_RE, _MOTOR_LABEL_RE):
        m = pat.search(text)
        if m:
            label = _fuel_value_to_label(m.group(1))
            if label:
                return label
    return None  # no label found, or labeled with an unrecognized value -- fall through


def extract_fuel(text: str) -> str | None:
    """Normalize fuel to Diesel/Petrol/Hybrid/PHEV/Electric/LPG/CNG or None.

    Checks the explicit "Palivo: <value>" label first (see
    _FUEL_LABEL_RE's own comment); only falls back to the free-text
    keyword rules below when no such label is present or its value isn't
    recognized.

    NOTE: engine badges are used as strong hints (TDI=>Diesel, TSI=>Petrol) but
    only when no explicit fuel word appears earlier in the ordered rule set.
    """
    if not text:
        return None
    labeled = _fuel_from_label(text)
    if labeled:
        return labeled
    for label, pat in _FUEL_RULES:
        if pat.search(text):
            return label
    return None


# ---- Power (kW) ---------------------------------------------------------- #
_POWER_KW = re.compile(r"(\d{2,4})\s*[\s]*k[wW]\b", re.I)
_POWER_KW_LABEL = re.compile(r"v[yý]kon\s*[:\-]?\s*(\d{2,4})\s*k[wW]", re.I)
_POWER_PS = re.compile(r"(\d{2,4})\s*(?:ps|k\.?s\.?|hp|koni)\b", re.I)


def extract_power(text: str) -> tuple[int | None, bool]:
    """Extract engine power in kW. Returns (kw, converted_from_ps).

    Prefers an explicit kW value. Only if none exists does it convert PS->kW
    (1 PS = 0.7355 kW), rounding to the nearest kW and flagging the conversion so
    downstream code knows the value is derived, not stated.
    """
    if not text:
        return None, False

    m = _POWER_KW_LABEL.search(text) or _POWER_KW.search(text)
    if m:
        kw = int(m.group(1))
        if 20 <= kw <= 1500:
            return kw, False

    m = _POWER_PS.search(text)
    if m:
        ps = int(m.group(1))
        if 30 <= ps <= 2000:
            return round(ps * 0.7355), True

    return None, False


# ---- Engine / variant ---------------------------------------------------- #
# Displacement + family badge, e.g. '2.0 TDI', '1.5 tsi', '2.0 TDI 147kW'.
_ENGINE_PAT = re.compile(
    r"\b(\d\.\d)\s*"
    r"(tdi|tsi|tfsi|fsi|mpi|dci|hdi|cdi|crdi|cdti|d4d|gtdi|t?dci|"
    r"benzin|diesel|multijet|bluehdi|ecoboost|vtec)\b",
    re.I,
)
# Displacement-only fallback, e.g. '2.0' immediately followed by a fuel hint word.
_DISPLACEMENT = re.compile(r"\b(\d\.\d)\s*l?\b", re.I)


def extract_engine(text: str) -> str | None:
    """Extract 'displacement + badge' engine string, normalized to e.g. '2.0 TDI'.

    Casing normalized (badge uppercased, known long words title-cased). Returns
    None rather than inventing a variant when nothing explicit is present. We do
    NOT attempt full trim-level decoding in this phase.
    """
    if not text:
        return None
    m = _ENGINE_PAT.search(text)
    if m:
        disp = m.group(1)
        badge = m.group(2).upper()
        # A couple of long marketing names read better title-cased than shouted.
        long_names = {"MULTIJET": "Multijet", "BLUEHDI": "BlueHDi",
                      "ECOBOOST": "EcoBoost", "BENZIN": "", "DIESEL": ""}
        if badge in long_names:
            badge = long_names[badge]
        return f"{disp} {badge}".strip()
    return None


# ---- Transmission -------------------------------------------------------- #
_TRANS_AUTO = re.compile(
    r"\b(dsg|automat(?:ick[aáyé])?|automatic|tiptronic|s[\s\-]?tronic|"
    r"steptronic|pdk|edc|dct|cvt|multitronic|cez\s*volant)\b", re.I,
)
_TRANS_MANUAL = re.compile(r"\b(manu[aá]l(?:n[aáyé])?|manual|man\.)\b", re.I)


def extract_transmission(text: str) -> str | None:
    """Normalize transmission to 'Automatic' or 'Manual' (or None).

    Automatic markers (incl. DSG and other auto-gearbox brand names) take
    precedence, because an ad may mention both words; a listing that says 'DSG
    automat' is automatic.
    """
    if not text:
        return None
    if _TRANS_AUTO.search(text):
        return "Automatic"
    if _TRANS_MANUAL.search(text):
        return "Manual"
    return None


# ---- Body type ----------------------------------------------------------- #
# Only extract when EXPLICITLY present (usually behind a 'Karoséria:' label or as
# a standalone recognized body word). We do NOT infer body type from the model.
_BODY_WORDS = {
    "suv": "SUV", "sedan": "Sedan", "hatchback": "Hatchback",
    "kombi": "Kombi", "combi": "Kombi", "liftback": "Liftback",
    "kupé": "Coupé", "kupe": "Coupé", "coupe": "Coupé",
    "kabriolet": "Kabriolet", "cabrio": "Kabriolet",
    "mpv": "MPV", "van": "Van", "pick-up": "Pick-up", "pickup": "Pick-up",
    "roadster": "Roadster",
}
_BODY_LABEL = re.compile(r"karos[eé]ria\s*[:\-]?\s*([a-zA-Záäčďéíĺľňóôŕšťúýž\-]+)", re.I)


def extract_body_type(text: str) -> str | None:
    """Extract body type only when explicitly stated. Returns normalized word/None."""
    if not text:
        return None
    m = _BODY_LABEL.search(text)
    if m:
        word = m.group(1).strip().lower()
        return _BODY_WORDS.get(word, word.capitalize())
    # Standalone recognized body word anywhere in the text (word-boundaried).
    for raw, norm in _BODY_WORDS.items():
        if re.search(rf"\b{re.escape(raw)}\b", text, re.I):
            return norm
    return None


# --------------------------------------------------------------------------- #
# Card parsing
# --------------------------------------------------------------------------- #
def _first_source(*checks: tuple[Any, str]) -> tuple[Any, str]:
    """Given (value, source) candidates in priority order, return the first hit.

    Each candidate is (value, source_label). Returns the first with a non-None
    value, else (None, SRC_MISSING).
    """
    for value, src in checks:
        if value is not None:
            return value, src
    return None, SRC_MISSING


def parse_card(card, search_query: str, search_page: int) -> dict | None:
    """Parse ONE Bazos result card (<div class="inzeraty inzeratyflex">).

    Structured fields come straight from HTML; spec fields are extracted from the
    description first, then the title, recording provenance for each.
    """
    a = card.select_one("h2.nadpis a")
    if not a or not a.get("href"):
        return None
    href = a["href"]
    m = re.search(r"/inzerat/(\d+)/", href)
    if not m:
        return None
    listing_id = m.group(1)
    url = href if href.startswith("http") else BASE + href
    title = a.get_text(" ", strip=True) or None

    # Price: ONLY from div.inzeratycena (never from popis -> avoids 'Cena bez DPH').
    price = None
    price_el = card.select_one("div.inzeratycena")
    if price_el:
        price = clean_int(price_el.get_text(" ", strip=True))

    # Listing date: span.velikost10 -> "[11.8. 2026]" (bump/refresh date).
    listing_date = None
    date_el = card.select_one("span.velikost10")
    if date_el:
        dm = re.search(r"\[([^\]]+)\]", date_el.get_text(" ", strip=True))
        if dm:
            listing_date = dm.group(1).strip()

    # Free-form description.
    popis_el = card.select_one("div.popis")
    desc = popis_el.get_text(" ", strip=True) if popis_el else ""
    desc = re.sub(r"\s+", " ", desc).strip()
    title_text = title or ""

    # --- spec extraction with provenance (description first, then title) ---
    year, year_src = _first_source(
        (extract_year(desc), SRC_DESC),
        (extract_year(title_text), SRC_TITLE),
    )
    km, km_src = _first_source(
        (extract_km(desc), SRC_DESC),
        (extract_km(title_text), SRC_TITLE),
    )
    fuel, fuel_src = _first_source(
        (extract_fuel(desc), SRC_DESC),
        (extract_fuel(title_text), SRC_TITLE),
    )
    transmission, trans_src = _first_source(
        (extract_transmission(desc), SRC_DESC),
        (extract_transmission(title_text), SRC_TITLE),
    )
    engine, engine_src = _first_source(
        (extract_engine(desc), SRC_DESC),
        (extract_engine(title_text), SRC_TITLE),
    )
    body_type, body_src = _first_source(
        (extract_body_type(desc), SRC_DESC),
        (extract_body_type(title_text), SRC_TITLE),
    )

    # Power carries an extra 'converted from PS' flag; try desc then title.
    power, power_src = None, SRC_MISSING
    kw_d, conv_d = extract_power(desc)
    kw_t, conv_t = extract_power(title_text)
    if kw_d is not None:
        power = kw_d
        power_src = SRC_DESC + ("_from_ps" if conv_d else "")
    elif kw_t is not None:
        power = kw_t
        power_src = SRC_TITLE + ("_from_ps" if conv_t else "")

    return {
        "source": "bazos",
        "listing_id": listing_id,
        "title": title,
        "year": year,
        "km": km,
        "price": price,
        "fuel": fuel,
        "transmission": transmission,
        "engine": engine,
        "power": power,
        "body_type": body_type,
        "listing_date": listing_date,
        "url": url,
        "search_query": search_query,
        "search_page": search_page,
        "raw_description": desc or None,
        "filter_reason": None,
        "year_source": year_src,
        "km_source": km_src,
        "fuel_source": fuel_src,
        "transmission_source": trans_src,
        "engine_source": engine_src,
        "power_source": power_src,
        "body_type_source": body_src,
    }


def parse_page(html: str, search_query: str, search_page: int) -> list[dict]:
    """Parse all result cards on one Bazos search page."""
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("div.inzeraty.inzeratyflex")
    rows = []
    for c in cards:
        row = parse_card(c, search_query, search_page)
        if row is not None:
            rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# Orchestration: multi-page retrieval with dedup + conservative delay
# --------------------------------------------------------------------------- #
@dataclass
class RetrieveResult:
    rows: list[dict] = field(default_factory=list)
    pages_fetched: int = 0
    urls: list[str] = field(default_factory=list)
    stopped_reason: str = ""


def retrieve(
    query: str,
    max_pages: int = 5,
    delay: float = 1.5,
    radius_km: int = 25,
) -> RetrieveResult:
    """Retrieve up to `max_pages` Bazos pages for `query`, deduped by listing ID.

    Stops early on: empty page, fewer-than-full page (no more results),
    max_pages reached, or a BlockedError (which propagates to the caller).
    """
    result = RetrieveResult()
    seen: set[str] = set()

    for page in range(1, max_pages + 1):
        url = build_search_url(query, crp=crp_for_page(page), radius_km=radius_km)
        if delay and page > 1:
            time.sleep(delay)
        html = fetch(url)  # may raise BlockedError -> caller stops & reports
        result.pages_fetched += 1
        result.urls.append(url)

        page_rows = parse_page(html, search_query=query, search_page=page)
        if not page_rows:
            result.stopped_reason = f"no listings on page {page}"
            break

        new_rows = [r for r in page_rows if r["listing_id"] not in seen]
        for r in new_rows:
            seen.add(r["listing_id"])
        result.rows.extend(new_rows)

        if len(page_rows) < RESULTS_PER_PAGE:
            result.stopped_reason = (
                f"last page reached (page {page} had {len(page_rows)} < "
                f"{RESULTS_PER_PAGE})"
            )
            break
    else:
        result.stopped_reason = f"max_pages ({max_pages}) reached"

    return result


def to_dataframe(rows: list[dict]):
    if pd is None:
        raise ImportError("pandas is not installed; `uv pip install pandas`.")
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 4: deterministic Bazos.sk retrieval + parser."
    )
    parser.add_argument("--query", default="Škoda Kodiaq 2.0 TDI",
                        help="Free-text search query.")
    parser.add_argument("--max-pages", type=int, default=3,
                        help="Maximum result pages to fetch (default 3).")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Delay between page requests, seconds (default 1.5).")
    parser.add_argument("--csv", default="bazos_phase4_candidates.csv",
                        help="Output CSV path.")
    args = parser.parse_args(argv)

    try:
        res = retrieve(args.query, max_pages=args.max_pages, delay=args.delay)
    except BlockedError as exc:
        print("\n*** STOPPED: possible block / anti-bot challenge ***", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2

    df = to_dataframe(res.rows)
    df.to_csv(args.csv, index=False)

    print(f"Query         : {args.query}")
    print(f"Pages fetched : {res.pages_fetched}")
    print(f"Stop reason   : {res.stopped_reason}")
    print(f"Raw listings  : {len(df)}")
    print(f"Saved CSV     : {args.csv}\n")

    # ---- field-availability report --------------------------------------
    total = len(df)
    print("Successfully parsed (non-null / total):")
    for col in ["year", "km", "price", "fuel", "engine", "power",
                "transmission", "body_type", "listing_date"]:
        print(f"  {col:14}: {df[col].notna().sum()}/{total}")

    print("\nProvenance breakdown (where each spec field came from):")
    for f in SPEC_FIELDS:
        sc = df[f"{f}_source"].value_counts().to_dict()
        print(f"  {f:12}: {sc}")

    # ---- 10 raw-description -> parsed examples ---------------------------
    print("\n" + "=" * 78)
    print("EXAMPLES: raw description -> parsed fields (first 10)")
    print("=" * 78)
    for _, r in df.head(10).iterrows():
        print(f"\n[{r['listing_id']}] {(r['title'] or '')[:60]}")
        print(f"  price={r['price']} year={r['year']} km={r['km']} "
              f"fuel={r['fuel']} engine={r['engine']} power={r['power']} "
              f"trans={r['transmission']} body={r['body_type']}")
        print(f"  raw: {(r['raw_description'] or '')[:200]}")

    # ---- surface FAILURES / ambiguity (do not hide) ---------------------
    print("\n" + "=" * 78)
    print("PARSING GAPS: listings missing one or more key spec fields")
    print("=" * 78)
    key = ["year", "km", "fuel", "engine", "power", "transmission"]
    gap = df[df[key].isna().any(axis=1)]
    print(f"{len(gap)}/{total} listings have >=1 missing key field.\n")
    for _, r in gap.head(12).iterrows():
        missing = [k for k in key if pd.isna(r[k])]
        print(f"[{r['listing_id']}] missing={missing}")
        print(f"  title: {(r['title'] or '')[:70]}")
        print(f"  raw  : {(r['raw_description'] or '')[:180]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
