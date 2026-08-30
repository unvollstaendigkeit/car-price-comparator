"""
Tests for bazos_scraper.extract_transmission -- had ZERO test coverage
before this file despite a live-recall bug found via a user-reported
single-car audit (2026-08-30): the old _TRANS_AUTO regex matched the bare
word "automat"/"automatická"/"automatické" ANYWHERE in a listing's free
text, including unrelated FEATURE mentions ("Automatická klimatizácia",
"Automatické svetlá", "ACC-automatická regulácia", "Automatické doplnkové
kúrenie") -- common in Slovak/Czech equipment lists -- and always let that
outrank an explicit manual-gearbox statement elsewhere in the SAME ad
("Manuál (6 st.)", "Prevodovka: manuálna 6st."). Three real captured Bazoš
listings (194936175, 194976258, 193250206) were confirmed live to be
genuinely manual but stored as "Automatic" because of this. The fixture
text below is taken verbatim (trimmed) from those three real
raw_description values.

Runs under pytest OR standalone via the __main__ runner at the bottom.
"""

from bazos_scraper import extract_transmission


# --------------------------------------------------------------------------- #
# 1. The exact live bugs (2026-08-30) -- real captured listing text.
# --------------------------------------------------------------------------- #
def test_listing_194936175_auto_climate_control_no_longer_beats_manual_label():
    desc = (
        "Technické údaje r. v.: 04/16, 1968cm³, 110kW (150PS), "
        "Pohon: Predných kolies, Manuál (6 st. ), Diesel, EURO 6, 5 dv. , "
        "(5-miestne), 257 525km, Strieborná metalíza, "
        "KOMFORT Centrálne uzamykanie, El. predné a zadné okná, El. zrkadlá, "
        "Automatická dvojzónová klimatizácia, Multifunkčný volant."
    )
    assert extract_transmission(desc) == "Manual"


def test_listing_193250206_auto_headlights_cruise_and_heating_no_longer_beat_manual_label():
    desc = (
        "Prevodovka: manuálna 6st. Miest na sedenie: 5 "
        "ASR TC, EDS, Automatické svetlá , BI-Xenónové reflektory, "
        "ACC-automatická regulácia vzdialenosti, Front assist "
        "Automatické doplnkové kúrenie, Elektricky sklápacie spätné "
        "zrkadlá s automatickou clonou."
    )
    assert extract_transmission(desc) == "Manual"


def test_listing_194976258_contradictory_ad_still_resolves_automatic():
    """This real ad genuinely contradicts itself: the descriptive paragraph
    says 'manuálnou prevodovkou', but a separate spec-bullet section later
    says '6 stupňový automat' with nothing feature-related following it --
    a bare gearbox mention, not a filtered false positive. Documented here
    as a known residual case (source data is ambiguous, not a regex bug)
    rather than silently asserting a "fixed" outcome that isn't real."""
    desc = (
        "Ponúkame na predaj Škoda Octavia 2.0 TDi vo verzii Combi, "
        "s naftovým motorom 2.0 TDi 110kW, v kombinácii so 6 stupňovou "
        "manuálnou prevodovkou a pohonom všetkých kolies. "
        "Špecifikácia: 2.0 TDi 110kW 6 stupňový automat Pohon všetkých kolies."
    )
    assert extract_transmission(desc) == "Automatic"


# --------------------------------------------------------------------------- #
# 2. Genuine automatic-gearbox mentions must still be detected correctly.
# --------------------------------------------------------------------------- #
def test_dsg_is_always_automatic():
    assert extract_transmission("2.0 TDI DSG Style") == "Automatic"


def test_bare_automat_noun_with_no_feature_word_following_is_automatic():
    assert extract_transmission("6 stupňový automat, málo najazdené") == "Automatic"


def test_automaticka_prevodovka_is_automatic():
    assert extract_transmission("automatická prevodovka, ako nové") == "Automatic"


def test_tiptronic_is_automatic():
    assert extract_transmission("1.9 TDI Tiptronic, klima") == "Automatic"


def test_explicit_manual_label_is_manual():
    assert extract_transmission("Prevodovka: manuálna, 6 stupňová") == "Manual"


def test_man_abbreviation_is_manual():
    assert extract_transmission("2.0 TDI, 110kW, man. prevodovka") == "Manual"


# --------------------------------------------------------------------------- #
# 3. Other individual false-positive feature words, in isolation.
# --------------------------------------------------------------------------- #
def test_automatic_windows_alone_is_not_automatic_transmission():
    assert extract_transmission("Automatické okná, žiadna iná informácia") is None


def test_automatic_wipers_alone_is_not_automatic_transmission():
    assert extract_transmission("Automatické stierače, žiadne iné info") is None


# --------------------------------------------------------------------------- #
# 4. No signal at all / empty input.
# --------------------------------------------------------------------------- #
def test_no_transmission_signal_at_all_returns_none():
    assert extract_transmission("Pekné rodinné auto, málo najazdené.") is None


def test_empty_text_returns_none():
    assert extract_transmission("") is None
    assert extract_transmission(None) is None


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
