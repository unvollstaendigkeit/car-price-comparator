"""
Deterministic unit tests for the Bazos extraction functions.

Each extractor is tested with:
  - POSITIVE cases: strings seen (or realistically shaped) in real listings.
  - NEGATIVE cases: strings that must NOT produce a value — especially guarding
    against reading an unrelated number (mileage, price, cc, PS) as a year, etc.

Run:  python -m pytest test_bazos_parser.py -q
  or: python test_bazos_parser.py   (falls back to a tiny built-in runner)
"""

from __future__ import annotations

from bazos_scraper import (
    extract_year,
    extract_km,
    extract_fuel,
    extract_power,
    extract_engine,
    extract_transmission,
    extract_body_type,
)


# --------------------------------------------------------------------------- #
# Year
# --------------------------------------------------------------------------- #
def test_year_positive():
    assert extract_year("Modelový rok: 2023") == 2023
    assert extract_year("Rok výroby: 05/2021") == 2021
    assert extract_year("Rok výroby: 20.5.2022") == 2022
    assert extract_year("1. prihlásenie: 02/2023") == 2023
    assert extract_year("Rok: 2021") == 2021
    assert extract_year("Škoda Kodiaq 2.0 TDI rv:05/2022 DSG") == 2022


def test_year_fallback_dateforms():
    # Unlabelled month/year and dotted date (last-resort patterns).
    assert extract_year("ŠKODA KODIAQ STYLE DSG | 110 kW | 1/2022 | ODPOČET") == 2022
    assert extract_year("Kodiaq 05/2022 facelift") == 2022
    assert extract_year("prihlásené 20.5.2022, pekné") == 2022


def test_year_negative():
    # Mileage must never be read as a year.
    assert extract_year("Najazdené: 137 782 km") is None
    assert extract_year("Kilometre: 176 100 km") is None
    # Engine displacement / cc must not become a year.
    assert extract_year("Objem motora: 1 968 cm3") is None
    # Price must not become a year.
    assert extract_year("Cena 26 800 €") is None
    # A bare implausible/again-mileage number.
    assert extract_year("150000 km, super stav") is None
    # Empty / no year present.
    assert extract_year("Predám auto, volajte") is None


# --------------------------------------------------------------------------- #
# Mileage
# --------------------------------------------------------------------------- #
def test_km_positive():
    assert extract_km("Nájazd: 110 000km") == 110000
    assert extract_km("Kilometre: 176 100 km") == 176100
    assert extract_km("Najazdené: 137 782 km") == 137782
    assert extract_km("Najazdené kilometre: 93 097 km") == 93097
    assert extract_km("krásny stav, 150tis km") == 150000
    assert extract_km("37000km") == 37000


def test_km_dot_thousands():
    # Slovak dot as a thousands separator (previously dropped to 500 / missed).
    assert extract_km("nájazdom Len 100.500 km") == 100500
    assert extract_km("○IBA 109.000KM○TOP VÝBAVA") == 109000
    # Unit-word BEFORE the number: 'Najazdené km: 138 000', 'km: 159 999'.
    assert extract_km("Najazdené km: 138 000") == 138000
    assert extract_km("r.v.: 3/2017 km: 159 999 výkon: 140kw") == 159999


def test_km_consumption_guard():
    # Fuel-consumption 'L/100km' must NOT be read as 100 km of mileage.
    assert extract_km("SPOTREBA VOZIDLA (L/100km) 6.5") is None
    assert extract_km("spotreba 5,9 l/100km") is None


def test_km_negative():
    # A 4-digit year with no km unit/label must not be read as mileage.
    assert extract_km("Modelový rok: 2023") is None
    # Price (has € not km).
    assert extract_km("Cena: 26 800 €") is None
    # Engine cc labelled explicitly (cm3, not km).
    assert extract_km("Objem motora: 1968 cm3") is None
    assert extract_km("Predám Škoda Kodiaq") is None


# --------------------------------------------------------------------------- #
# Fuel
# --------------------------------------------------------------------------- #
def test_fuel_positive():
    assert extract_fuel("Palivo: Diesel") == "Diesel"
    assert extract_fuel("nafta, 2.0") == "Diesel"
    assert extract_fuel("2.0 TDI 110kw") == "Diesel"        # badge implies diesel
    assert extract_fuel("Benzín 1.5") == "Petrol"
    assert extract_fuel("1.5 TSI DSG") == "Petrol"          # badge implies petrol
    assert extract_fuel("Plug-in hybrid") == "PHEV"
    assert extract_fuel("hybridné vozidlo") == "Hybrid"
    assert extract_fuel("elektromobil, dojazd 400km") == "Electric"
    assert extract_fuel("palivo LPG + benzín")  # LPG wins (checked before petrol)
    assert extract_fuel("pohon CNG") == "CNG"


def test_fuel_glued_badge():
    # Badge glued to displacement (no space) must still resolve fuel.
    assert extract_fuel("Škoda Kodiaq Sportline 2023 2.0TDI 147KW 4x4") == "Diesel"
    assert extract_fuel("Kodiaq 2.0Tdi 140kw DSG") == "Diesel"
    assert extract_fuel("Octavia 1.5TSI DSG") == "Petrol"


def test_fuel_negative():
    assert extract_fuel("Predám auto, super stav") is None
    assert extract_fuel("") is None
    # A word merely CONTAINING a badge substring must not trigger (letter bounds).
    assert extract_fuel("studena klima, editsia") is None


def test_fuel_priority():
    # Plug-in must win over plain 'hybrid'.
    assert extract_fuel("Plug-in hybrid, benzín") == "PHEV"
    # LPG (often a bi-fuel petrol car) must be reported as LPG, not Petrol.
    assert extract_fuel("benzín/LPG") == "LPG"


# --------------------------------------------------------------------------- #
# Power
# --------------------------------------------------------------------------- #
def test_power_positive():
    assert extract_power("147kW") == (147, False)
    assert extract_power("147 kW") == (147, False)
    assert extract_power("Výkon: 147kW") == (147, False)
    assert extract_power("2.0 TDI 110kW DSG") == (110, False)
    # PS-only -> explicit conversion, flagged True.
    kw, converted = extract_power("200 PS")
    assert kw == round(200 * 0.7355) and converted is True


def test_power_prefers_kw_over_ps():
    # '147kW (200 PS)' must return the stated kW, not the converted PS.
    assert extract_power("147kW (200 PS)") == (147, False)


def test_power_negative():
    assert extract_power("super auto") == (None, False)
    # cc must not be read as kW.
    assert extract_power("Objem: 1968 cm3")[0] is None


# --------------------------------------------------------------------------- #
# Engine / variant
# --------------------------------------------------------------------------- #
def test_engine_positive():
    assert extract_engine("2.0 TDI") == "2.0 TDI"
    assert extract_engine("2.0 tdi 110kw") == "2.0 TDI"
    assert extract_engine("1.5 TSI DSG") == "1.5 TSI"
    assert extract_engine("Motorizácia: 2.0 tdi 147kW") == "2.0 TDI"


def test_engine_negative():
    # No recognizable displacement+badge -> None (do not invent).
    assert extract_engine("Škoda Kodiaq automat, super stav") is None
    assert extract_engine("") is None


# --------------------------------------------------------------------------- #
# Transmission
# --------------------------------------------------------------------------- #
def test_transmission_positive():
    assert extract_transmission("DSG") == "Automatic"
    assert extract_transmission("automatická prevodovka") == "Automatic"
    assert extract_transmission("7-st. automat") == "Automatic"
    assert extract_transmission("manuál 6 stupňov") == "Manual"
    assert extract_transmission("manuálna prevodovka") == "Manual"


def test_transmission_priority_and_negative():
    # 'DSG automat' is unambiguously automatic.
    assert extract_transmission("DSG automat") == "Automatic"
    assert extract_transmission("super auto, málo jazdené") is None


# --------------------------------------------------------------------------- #
# Body type
# --------------------------------------------------------------------------- #
def test_body_type_positive():
    assert extract_body_type("Karoséria: SUV") == "SUV"
    assert extract_body_type("kombi, strešné okno") == "Kombi"
    assert extract_body_type("pekný sedan") == "Sedan"


def test_body_type_negative():
    # Must NOT infer body type from model name / generic text.
    assert extract_body_type("Škoda Kodiaq 2.0 TDI") is None
    assert extract_body_type("super rodinné auto") is None


# --------------------------------------------------------------------------- #
# Tiny built-in runner (so it works without pytest installed).
# --------------------------------------------------------------------------- #
def _run_all() -> int:
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc!r}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {exc!r}")
    print(f"\n{passed} passed, {failed} failed, {len(fns)} total")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
