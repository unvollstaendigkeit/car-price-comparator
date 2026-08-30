"""
Tests for bazos_scraper.extract_fuel -- had ZERO test coverage before this
file despite being the source of two documented mislabeling bugs found via
live recall audits (2026-08-28: unshielded "elektricky <equipment>"
mentions mislabeled ~8% of all captured Bazoš listings as "Electric";
2026-08-30: an explicit "Palivo: Diesel" label was still outranked by an
unshielded "elektricky ovládatelný kufor" equipment mention elsewhere in
the SAME description, since _FUEL_RULES checked "Electric" before
"Diesel" with no regard for which one came from an authoritative labeled
field vs incidental free text).

Runs under pytest OR standalone via the __main__ runner at the bottom.
"""

from bazos_scraper import extract_fuel


# --------------------------------------------------------------------------- #
# 1. The exact live bug (2026-08-30): a labeled "Palivo: Diesel" field must
# win over an unrelated "elektricky <equipment>" mention elsewhere in the
# same description, regardless of which one appears first in the text.
# --------------------------------------------------------------------------- #
def test_palivo_diesel_label_beats_unrelated_electric_equipment_mention():
    desc = (
        "▶️Rok výroby: 5/2023 ▶️Objem motora: 1968cm3 ▶️Palivo: Diesel "
        "▶️Výkon: 110kW ▶️Prevodovka: 7st. DSG. "
        "Predné elektricky nastaviteľné sedačky s pamäťovou funkciou. "
        "Elektricky ovládatelný kufor. Vyhrievané + sklápatelné spätné zrkadlá."
    )
    assert extract_fuel(desc) == "Diesel"


def test_palivo_diesel_label_before_the_electric_mention_also_wins():
    """Same bug, but with the label appearing AFTER the equipment mention --
    label priority must not depend on text order."""
    desc = "Elektricky ovládatelný kufor. ▶️Palivo: Diesel ▶️Výkon: 110kW"
    assert extract_fuel(desc) == "Diesel"


# --------------------------------------------------------------------------- #
# 2. The "Palivo:" label recognizes every real value spelling seen in
# production data (sampled 2026-08-30 from 5,000 real descriptions).
# --------------------------------------------------------------------------- #
def test_palivo_label_value_spellings():
    for value, expected in [
        ("Diesel", "Diesel"),
        ("diesel", "Diesel"),
        ("Nafta", "Diesel"),
        ("nafta", "Diesel"),
        ("Benzín", "Petrol"),
        ("benzin", "Petrol"),
        ("Elektromotor", "Electric"),
        ("elektro", "Electric"),
        ("elektrina", "Electric"),
        ("Hybrid", "Hybrid"),
        ("hybridní", "Hybrid"),
        ("Plug-in", "PHEV"),
        ("LPG", "LPG"),
        ("CNG", "CNG"),
    ]:
        desc = f"Palivo: {value} Výkon: 100kW Prevodovka: manuál"
        assert extract_fuel(desc) == expected, f"{value} -> {extract_fuel(desc)}"


def test_unrecognized_palivo_value_falls_back_to_free_text_rules():
    """"Palivo: pozri popis" (see description) isn't a real value -- must
    fall through to the ordinary keyword rules, not return None outright
    when the rest of the text does have a real signal."""
    desc = "Palivo: pozri popis. Motor 2.0 TDI, 110kW, manuálna prevodovka."
    assert extract_fuel(desc) == "Diesel"


def test_motor_label_tsi_word_beats_unrelated_electric_equipment_mention():
    """The Motor: field variant of the exact same bug (2026-08-30, second
    pass): listing 194923025, a "1.5 TSI, benzín" Kodiaq stored as
    fuel='Electric' purely from an unrelated "Elektrické otváranie/
    zatváranie kufra" (electric boot) mention -- the "/" in that phrase
    even breaks the equipment-noun lookahead's own word tokenization."""
    desc = ("📍 Poprad ✅Motor: 1.5 TSI, benzín ✅Výkon: 110 kW "
             "✅Elektrické otváranie/zatváranie kufra "
             "✅Elektrické sklopné spätné zrkadlá")
    assert extract_fuel(desc) == "Petrol"


def test_motor_label_diesel_word_in_parentheses():
    assert extract_fuel("Motor: 1,5 DIESEL (81 kW) VIN: xxx") == "Diesel"


def test_motor_label_engine_badge_only_no_spelled_out_word():
    """"Motor: 1.5 dCi, 78 kW" has no spelled-out fuel word at all -- only
    the engine badge -- and must still resolve correctly."""
    desc = "Motor: 1.5 dCi, 78 kW Nájazd: 233 tis. km"
    assert extract_fuel(desc) == "Diesel"


def test_palivo_label_wins_over_conflicting_motor_label():
    desc = "Palivo: Diesel Motor: 1.5 TSI benzín"
    assert extract_fuel(desc) == "Diesel"


def test_compound_motor_value_defers_to_explicit_hybrid_word_elsewhere():
    """A compound Motor: value ("benzín + elektromotor") is ambiguous on
    its own, but this description also explicitly says "hybridný" --
    deferring to the free-text rules (not guessing from the label) must
    still land on the right, unambiguous answer here."""
    desc = "Motor: 1.6 benzín + elektromotor, hybridný pohon"
    assert extract_fuel(desc) == "Hybrid"


def test_compound_palivo_value_never_collapses_to_plain_combustion_fuel():
    """Regression guard: a real PHEV listing (192841079, found while
    fixing the Diesel-label bug) states "Palivo: benzín + elektro/SOH
    95,6%" -- collapsing that to plain "Petrol" would SILENTLY DISCARD
    the electric half entirely, which is worse than the bug being fixed.
    The label check must back off on a compound value and defer to the
    free-text rules -- exactly what happened before this label check
    existed, so this is a no-op, not a regression, for this shape of ad."""
    desc = ("Rok výroby: 2022/10 Objem motora: 1598 cm3 Palivo: benzín + "
             "elektro/SOH 95,6% Výkon motora: 133kw/181PS Prevodovka: 8st.-automat")
    # Not "Petrol" -- the label alone is ambiguous for a compound value.
    assert extract_fuel(desc) != "Petrol"


# --------------------------------------------------------------------------- #
# 3. Regression guard for the 2026-08-28/29 equipment-noun fixes -- must
# still work for descriptions with NO "Palivo:" label at all.
# --------------------------------------------------------------------------- #
def test_electric_equipment_mention_without_palivo_label_still_shielded():
    desc = "Škoda Octavia 2.0 TDI. Elektrické sklopné spätné zrkadlá, elektrické okná."
    assert extract_fuel(desc) == "Diesel"


def test_genuine_electric_car_without_palivo_label_still_detected():
    desc = "Škoda Enyaq iV, elektromobil, dojazd 400km."
    assert extract_fuel(desc) == "Electric"


def test_no_fuel_signal_at_all_returns_none():
    assert extract_fuel("Pekné rodinné auto, málo najazdené.") is None


def test_empty_text_returns_none():
    assert extract_fuel("") is None
    assert extract_fuel(None) is None


# --------------------------------------------------------------------------- #
# Standalone runner (works without pytest)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"PASS  {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    raise SystemExit(1 if failed else 0)
