"""
Standalone audit: finds every case where phase6_validate.rule_model_soft's
substring-based model matching would conflate two ACTUALLY DISTINCT Autobazar
models -- the same bug shape as Hyundai Ioniq vs Ioniq 5/6 (2026-08-28),
found here by generalizing to the whole dataset instead of one car at a time.

Autobazar's own structured search_model field is ground truth: it already
distinguishes "Ioniq" from "Ioniq 5" from "Ioniq 6" as three separate values
(confirmed for real data). So for every (brand, model) pair, this checks
every OTHER model under the same brand whose name contains `model` as a
substring, and flags it UNLESS it's already covered by
phase6_validate._DISTINCT_MODEL_LINE_SUFFIXES/_PREFIXES.

Deliberately Autobazar-only: Bazoš has no structured per-listing model
field (free-text search_query), so there's no ground truth to diff against
the same precise way -- a real limitation, not an oversight.

Usage:
    python3 audit_model_line_contamination.py
"""
from __future__ import annotations

import re
import sys

from market_provider import MARKET_COLLECTOR_DB_PATH
from phase6_validate import _BRAND_ALIASES, _model_line_excluded, _model_slug_candidates


def _connect_readonly(db_path: str):
    import sqlite3
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


# Body-style / trim-badge words that commonly get appended to a model name
# on Autobazar's own taxonomy WITHOUT denoting a genuinely different model --
# these are exactly the kind of thing body_type (or, for badges, variant)
# already covers, non-blocking, per the 2026-08-28 business sign-off. A
# candidate whose ENTIRE extra suffix is built from these words is noise,
# not a contamination bug, and is filtered out so the report stays
# high-signal instead of dominated by "Octavia" vs "Octavia Combi".
_BODY_STYLE_OR_TRIM_WORDS = {
    "combi", "kombi", "variant", "avant", "touring", "tourer", "kupe", "kupé",
    "coupe", "coupé", "cabriolet", "cabrio", "sedan", "st", "sw", "break",
    "sportback", "cw", "fastback", "grandtour", "spaceback", "shooting",
    "brake", "allroad", "van", "furgon", "s-cross", "caravan", "long",
    "wagon", "weekend", "hatchback", "liftback", "roadster", "spider",
    "sport", "grand", "spacetourer", "picasso", "tepee", "gt", "gti", "gte",
    "r", "rs", "s", "line", "plus", "xl", "city",
}


def _extra_suffix_is_noise(model: str, other: str) -> bool:
    """True if `other` is just `model` plus a trailing run of known
    body-style/trim words (in any order/count) -- e.g. "Octavia" vs
    "Octavia Combi", "A6" vs "A6 Avant", "Rad 3" vs "Rad 3 GT"."""
    m, o = model.lower(), other.lower()
    if not o.startswith(m):
        return False
    extra = o[len(m):].strip()
    if not extra:
        return False
    tokens = re.split(r"[\s/()-]+", extra)
    return all(tok in _BODY_STYLE_OR_TRIM_WORDS for tok in tokens if tok)


def _already_excluded(brand_aliases: set[str], model: str, other: str) -> bool:
    """True if this exact (model, other) pair is already covered by the
    existing exclusion table -- reuses phase6_validate._model_line_excluded
    DIRECTLY (not a re-implementation) so this audit can never silently
    drift out of sync with what rule_model_soft actually checks, the way a
    duplicated copy of this logic did during this same audit's development
    (missed the fused-form entries added alongside it until this was
    switched to a direct import)."""
    m, o = model.lower(), other.lower()
    return any(_model_line_excluded(alias, m, o) for alias in brand_aliases)


def audit(db_path: str = MARKET_COLLECTOR_DB_PATH, min_listings: int = 3) -> int:
    conn = _connect_readonly(db_path)
    try:
        rows = conn.execute(
            "SELECT search_brand, search_model, COUNT(*) FROM autobazar_listings "
            "WHERE parse_mode = 'sitemap_detail' AND search_brand != '' "
            "AND search_model != '' "
            "GROUP BY search_brand, search_model"
        ).fetchall()
    finally:
        conn.close()

    by_brand: dict[str, dict[str, int]] = {}
    for brand, model, count in rows:
        by_brand.setdefault(brand, {})[model] = count

    problems = []
    for brand, models in by_brand.items():
        brand_key = brand.strip().lower()
        brand_aliases = _BRAND_ALIASES.get(brand_key, {brand_key})
        names = list(models)
        for model in names:
            if models[model] < min_listings:
                continue  # too rare to matter / too rare to trust as a real model
            m_lower = model.lower()
            # Would rule_model_soft's candidate-substring check treat `other`
            # as a match for `model`? Reuse the exact same candidate logic.
            candidates = set(_model_slug_candidates(model)) | {m_lower}
            for other in names:
                if other == model or models[other] < min_listings:
                    continue
                o_lower = other.lower()
                hit = any(
                    cand in o_lower or cand.replace(" ", "") in o_lower or cand.replace("-", " ") in o_lower
                    for cand in candidates
                )
                if not hit:
                    continue
                if _already_excluded(brand_aliases, model, other):
                    continue
                if _extra_suffix_is_noise(model, other):
                    continue
                problems.append((brand, model, models[model], other, models[other]))

    print(f"Checked {sum(len(m) for m in by_brand.values())} (brand, model) pairs "
          f"across {len(by_brand)} brands (min {min_listings} listings each).\n")

    if not problems:
        print("No unflagged contamination candidates found.")
        return 0

    print(f"CANDIDATES ({len(problems)}) -- searching for the first model would "
          f"currently also admit listings that are structurally a DIFFERENT "
          f"model on Autobazar's own taxonomy:\n")
    seen = set()
    for brand, model, mcount, other, ocount in sorted(problems, key=lambda p: -p[4]):
        key = (brand, model, other)
        if key in seen:
            continue
        seen.add(key)
        print(f"  {brand} {model!r} ({mcount}) would also match {brand} {other!r} ({ocount})")
    return 1


if __name__ == "__main__":
    sys.exit(audit())
