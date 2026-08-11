# Targeted Rerun: Before/After Findings

Scope: 20 cars flagged as affected by retrieval/robustness issues in the
original full run (`phase7_out/`). Rerun output: `phase7_rerun2/`.
All four fixes were made in `phase6_validate.py` / `phase5_compare.py` and are
covered by regression tests in `test_retrieval_and_outliers.py` (22 tests, all
passing).

## Fixes applied

1. **Autobazar model-slug resolution (general, self-maintaining).**
   Replaced the tiny hardcoded slug map with a resolver that reads Autobazar's
   own model taxonomy (`label` -> `sefName`) from the brand page `__NEXT_DATA__`,
   plus deterministic candidate construction (`3 Series` -> `rad-3`,
   `A-Class` -> `a-trieda`, `GLA-Class` -> `gla`, `ID.3` -> `id3`). Falls back to
   keyword search when a model genuinely has no category (e.g. bare BMW
   `2-series` 404s -> keyword fallback, note recorded).

2. **Bazos query normalization.** Strip recall-killing suffixes
   (`-Class`/`-Klasse`/` Series`) so `Mercedes GLA-Class` (~5 ads) becomes
   `Mercedes GLA` (full page). Also distinguishes a fetch BLOCK (real error) from
   a successful-but-empty result ("no listings" note).

3. **Undervaluation formula fix.** `undervaluation_pct` is now measured against
   the **asking price**, not the market median. The old median denominator made
   overpriced cars explode to nonsense (VW Touran -229.7%); it now reads a sane
   -69.7% ("priced ~70% above this thin sample"). Sign convention: positive =
   below market (a deal), negative = above market.

4. **Thin-sample / outlier protection.** Single-listing (n=1) estimates are
   downgraded to low confidence with a warning; implausible market/asking ratios
   (< 0.20 or > 5.0) are suppressed as parse artifacts.

## Before -> After highlights

| Car | AB comps | Confidence | Note |
|-----|----------|-----------|------|
| BMW 3 Series (rad-3) | 0 -> 3 | INSUFFICIENT (thin) | slug fixed, comps now flow |
| Mercedes GLA-Class -> gla | 0 -> 4 | INSUFFICIENT -> **MEDIUM** | slug + query fixed |
| Mercedes B-Class | 0 -> 2 | INSUFFICIENT | slug fixed |
| Mercedes A-Class -> a-trieda | 0 -> 3 | INSUFFICIENT (thin) | slug fixed |
| VW Touran | 4 -> 4 | MEDIUM | %diff -229.7% -> **-69.7%** (formula) |
| BMW 2 Series | 0 -> 0 | INSUFFICIENT -> LOW | no bare category; keyword fallback (note recorded) |

Result: **5 previously-broken slugs now return comparables**, the Touran and
Mazda 3 extreme percentages are corrected, and every remaining `AB=0` is now
explained (see below) rather than silently empty.

## Honest conclusion: remaining zeros are market scarcity, not bugs

After the slug fixes, previously-broken models now retrieve a **full 60 listings**
each (VW Golf, VW Passat, Skoda Superb, etc.). They still resolve to 0
comparables because the **matching** rules legitimately reject the pool:

- **VW Golf (#24)** is labelled **PHEV** in inventory; virtually no Slovak Golf
  listings are plug-in hybrid -> fuel blocks 59/60. (Also a rare 150 kW / 177k km
  combo -> power blocks 52/60.) This spec is genuinely scarce, possibly a
  mislabel in the source inventory.
- **VW Passat (#15) / Skoda Superb (#12)** are **Petrol**; both models are
  overwhelmingly **Diesel** on the Slovak market -> fuel is the top blocker
  (50/60, 46/60), compounded by km/year/power at these thin intersections.

Loosening the fuel rule to force matches would compare petrol cars against
diesels and manufacture false "market prices" - worse than an honest
INSUFFICIENT flag. The correct behavior is exactly what the tool now does:
retrieve broadly, match strictly, and report scarcity transparently.

## Suggested next step (not a bug)

The real lever for these cars is **inventory data quality** (confirm the Golf's
PHEV label) and, optionally, a documented "fuel-relaxed, flagged" fallback tier
for cars whose exact fuel has no local market - clearly labelled as a
cross-fuel estimate so the user knows it is weaker evidence.
