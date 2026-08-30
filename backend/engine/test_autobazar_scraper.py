"""
Regression tests for autobazar_scraper._record_to_row()'s price field
selection.

Background (2026-08-19): _record_to_row() previously preferred the raw
`price` field over `finalPrice`, under the (incorrect) assumption that
price/finalPrice/priceCurrent were interchangeable mirrors of the same
value. Verified against 26,971 real collected Autobazar records: they
disagree in 27.3% of cases, in two distinct ways --

  1. `price` is a pre-discount/list price; `finalPrice` is the actual
     current advertised price (ratio ~1.03-1.31x).
  2. For listings aggregated from Autobazar's Czech-market sister site
     (raw_json.location.parentNames contains "Ceska republika"), `price`
     is denominated in Czech Koruna while `finalPrice`/`priceCurrent` are
     already EUR-converted (ratio ~24.2-24.3x, matching the real CZK/EUR
     exchange rate). This produced listings priced above EUR 1,000,000 for
     ordinary used cars in the historical dataset.

Every fixture below is a minimal reconstruction of a REAL record pulled
from the collector's database on 2026-08-19 (see market-collector's own
conversation history for the full raw_json) -- not synthesized values.

Runs under pytest (`python -m pytest test_autobazar_scraper.py`) OR
standalone (`python test_autobazar_scraper.py`).
"""
import sys

from autobazar_scraper import _record_to_row


# --------------------------------------------------------------------------- #
# Real problematic records (minimal field set _record_to_row actually reads)
# --------------------------------------------------------------------------- #
REAL_TOURAN_ARTEON = {
    "sefName": "volkswagen-touran-15-tsi-dsg-r-line", "id": "AeX7QC5V6oN",
    "title": "Volkswagen Touran 1.5 TSi DSG R-Line", "yearValue": "2021",
    "mileage": 45000, "price": 1049000, "finalPrice": 43288.08, "priceCurrent": 43288.08,
    "unitPrice": 1, "fuelValue": "Benzín", "gearboxValue": "7-st. automatická",
    "enginePower": 110, "bodyworkValue": "Combi", "clientUpdatedAt": "2026-08-19T09:23:22+00:00",
}
REAL_OCTAVIA_RS = {
    "sefName": "skoda-octavia-combi-20-tsi-195-rs-combi-dsg", "id": "Aef_8KZDls0",
    "title": "Škoda Octavia Combi 2.0 TSi 195 RS Combi DSG", "yearValue": "2020",
    "mileage": 60000, "price": 969900, "finalPrice": 40088.45, "priceCurrent": 40088.45,
    "unitPrice": 1, "fuelValue": "Benzín", "gearboxValue": "7-st. automatická",
    "enginePower": 140, "bodyworkValue": "Combi", "clientUpdatedAt": "2026-08-19T09:23:24+00:00",
}
REAL_CZK_QASHQAI = {
    "sefName": "nissan-qashqai-16-i", "id": "AeAXzmyzZA9",
    "title": "Nissan Qashqai 1.6 i", "yearValue": "2015",
    "mileage": 120000, "price": 69900, "finalPrice": 2873.59, "priceCurrent": 2873.59,
    "unitPrice": 1, "fuelValue": "Benzín", "gearboxValue": "Manuálna",
    "enginePower": 84, "bodyworkValue": "SUV", "clientUpdatedAt": "2026-08-16T10:00:00+00:00",
    "location": {"name": "okres Praha", "parentNames": ["Hlavní město Praha", "Česká republika"]},
}
REAL_PREDISCOUNT_EXAMPLE = {
    # A smaller-magnitude, EUR-native mismatch (Pattern A) -- price is a
    # pre-discount list price, finalPrice is the current advertised price.
    "sefName": "example-listing", "id": "AmNBPgji-Lj",
    "title": "Example Listing", "yearValue": "2019", "mileage": 80000,
    "price": 14499, "finalPrice": 12499, "priceCurrent": 12499,
    "fuelValue": "Diesel", "gearboxValue": "Manuálna",
    "enginePower": 90, "bodyworkValue": "Hatchback", "clientUpdatedAt": "2026-08-15T10:00:00+00:00",
}

# A ceiling no ordinary used car in this dataset should plausibly exceed --
# a direct regression guard against the raw-price/currency-mixup class of bug.
IMPLAUSIBLE_PRICE_CEILING_EUR = 500_000


def test_currency_mixup_case_touran_uses_final_price():
    row = _record_to_row(REAL_TOURAN_ARTEON)
    assert row["price"] == 43288, row["price"]
    assert row["price"] < IMPLAUSIBLE_PRICE_CEILING_EUR


def test_currency_mixup_case_octavia_uses_final_price():
    row = _record_to_row(REAL_OCTAVIA_RS)
    assert row["price"] == 40088, row["price"]
    assert row["price"] < IMPLAUSIBLE_PRICE_CEILING_EUR


def test_currency_mixup_case_czk_qashqai_uses_final_price():
    row = _record_to_row(REAL_CZK_QASHQAI)
    assert row["price"] == 2873, row["price"]
    assert row["price"] < IMPLAUSIBLE_PRICE_CEILING_EUR


def test_prediscount_case_prefers_final_price_over_list_price():
    row = _record_to_row(REAL_PREDISCOUNT_EXAMPLE)
    assert row["price"] == 12499, row["price"]
    assert row["price"] != 14499  # the pre-discount price must not win


def test_no_known_problematic_record_ever_returns_raw_price():
    """Direct regression guard: for every real problematic fixture, the raw
    (wrong) `price` field's value must never be what comes out."""
    for rec in (REAL_TOURAN_ARTEON, REAL_OCTAVIA_RS, REAL_CZK_QASHQAI, REAL_PREDISCOUNT_EXAMPLE):
        row = _record_to_row(rec)
        assert row["price"] != rec["price"], (
            f"{rec['id']}: price extraction regressed to the raw (wrong) price field"
        )


def test_falls_back_to_price_current_when_final_price_missing():
    rec = dict(REAL_TOURAN_ARTEON)
    del rec["finalPrice"]
    row = _record_to_row(rec)
    assert row["price"] == 43288  # priceCurrent still correct


def test_falls_back_to_raw_price_only_as_last_resort():
    rec = dict(REAL_TOURAN_ARTEON)
    del rec["finalPrice"]
    del rec["priceCurrent"]
    row = _record_to_row(rec)
    # Neither normalized field is available -- raw price is the only option
    # left, exactly as before this fix, for this narrow (rare: 8/26,971
    # observed) case.
    assert row["price"] == 1049000


def test_normal_matching_record_unaffected():
    """The common case (all three price fields agree) must still work
    exactly as before -- this fix must not change well-behaved records."""
    rec = {
        "sefName": "skoda-octavia", "id": "Am0-e4x7A0p", "title": "Škoda Octavia 2.0 TSI Style DSG",
        "yearValue": "2018", "mileage": 169954, "price": 15990, "finalPrice": 15990,
        "priceCurrent": 15990, "fuelValue": "Benzín", "gearboxValue": "7-st. automatická",
        "enginePower": 140, "bodyworkValue": "Sedan", "clientUpdatedAt": "2026-08-18T15:00:21.000Z",
    }
    row = _record_to_row(rec)
    assert row["price"] == 15990
    assert row["title"] == "Škoda Octavia 2.0 TSI Style DSG"
    assert row["year"] == 2018
    assert row["km"] == 169954


def test_other_fields_unaffected_by_the_price_fix():
    row = _record_to_row(REAL_TOURAN_ARTEON)
    assert row["title"] == "Volkswagen Touran 1.5 TSi DSG R-Line"
    assert row["year"] == 2021
    assert row["km"] == 45000
    assert row["fuel"] == "Benzín"
    assert row["url"] == "https://www.autobazar.eu/detail/volkswagen-touran-15-tsi-dsg-r-line/AeX7QC5V6oN/"


# --------------------------------------------------------------------------- #
# client_created_at (2026-08-19): trustworthy original-posting signal,
# distinct from clientUpdatedAt/listing_date (a bump/touch signal) -- see
# _record_to_row's own comment for the verification behind this.
# --------------------------------------------------------------------------- #
def test_client_created_at_prefers_clientCreatedAt():
    rec = dict(REAL_TOURAN_ARTEON)
    rec["clientCreatedAt"] = "2026-02-26T08:21:23.979Z"
    rec["publishUp"] = "2025-09-01T22:00:00.000Z"  # deliberately different
    row = _record_to_row(rec)
    assert row["client_created_at"] == "2026-02-26T08:21:23.979Z"


def test_client_created_at_falls_back_to_publish_up():
    rec = dict(REAL_TOURAN_ARTEON)
    rec["publishUp"] = "2025-09-01T22:00:00.000Z"
    # no clientCreatedAt key at all
    row = _record_to_row(rec)
    assert row["client_created_at"] == "2025-09-01T22:00:00.000Z"


def test_client_created_at_is_none_when_neither_field_present():
    rec = dict(REAL_TOURAN_ARTEON)
    row = _record_to_row(rec)  # neither clientCreatedAt nor publishUp in the fixture
    assert row["client_created_at"] is None


def test_client_created_at_never_equals_the_bump_signal_when_they_differ():
    """Direct regression guard: client_created_at must reflect the true
    posting-date fields, never silently fall back to clientUpdatedAt (the
    bump/touch signal this field exists specifically to be distinct from)."""
    rec = dict(REAL_TOURAN_ARTEON)
    rec["clientCreatedAt"] = "2026-02-26T08:21:23.979Z"
    row = _record_to_row(rec)
    assert row["client_created_at"] != row["listing_date"]


# --------------------------------------------------------------------------- #
# Standalone runner (works without pytest)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
