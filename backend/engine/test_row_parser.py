"""
Tests for the paste-a-row parsing layer (row_parser.parse_pasted_row).

Runs under pytest (`python -m pytest test_row_parser.py`) OR standalone
(`python test_row_parser.py`) via the __main__ runner at the bottom, so it
works whether or not pytest is installed in the environment.

Covers every case called out in the feature spec:
  * tab-separated Excel row without headers
  * the example row (multi-space separated)
  * Markdown table row
  * row + header
  * the other known table format (Format A, with header)
  * missing optional fields
  * ambiguous fields
  * malformed / partial paste
  * SOLD / non-numeric price
  * different date formats
  * different fuel labels
"""

from row_parser import parse_pasted_row


# --------------------------------------------------------------------------- #
# 1. Tab-separated Excel row, no header
# --------------------------------------------------------------------------- #
def test_tab_separated_no_header():
    row = "2019\tDiesel\tSkoda\tOctavia\t2.0 TDI 150HP\tEstate\t120000\t15500"
    r = parse_pasted_row(row)
    assert r.ok
    assert r.mode == "headerless"
    assert r.car["brand"] == "Skoda"
    assert r.car["model"] == "Octavia"
    assert r.car["year"] == 2019
    assert r.car["fuel"] == "Diesel"
    assert r.car["body_type"] == "Estate"
    assert r.car["km"] == 120000
    assert r.car["price"] == 15500
    assert "TDI" in (r.car["variant"] or "")


# --------------------------------------------------------------------------- #
# 2. The canonical example row (runs of spaces)
# --------------------------------------------------------------------------- #
def test_example_row_space_separated():
    row = ("10.7.2014    Diesel    VW    Polo    1,4 TDI BMT Highline 90HK 5d    "
           "hatchback    WVWZZZ6RZFY064440    Black    280,000    2,300    Ask for pictures")
    r = parse_pasted_row(row)
    assert r.ok
    assert r.car["year"] == 2014
    assert r.car["fuel"] == "Diesel"
    assert r.car["brand"] in ("VW", "Volkswagen")
    assert r.car["model"] == "Polo"
    assert r.car["variant"] == "1,4 TDI BMT Highline 90HK 5d"
    assert r.car["body_type"] == "Hatchback"
    assert r.car["km"] == 280000
    assert r.car["price"] == 2300
    assert r.extras["vin"] == "WVWZZZ6RZFY064440"
    assert r.extras["colour"] == "Black"


# --------------------------------------------------------------------------- #
# 3. Markdown table row
# --------------------------------------------------------------------------- #
def test_markdown_row():
    row = ("| 10.7.2014 | Diesel | VW | Polo | 1,4 TDI BMT Highline 90HK 5d | "
           "hatchback | WVWZZZ6RZFY064440 | Black | 280,000 | 2,300 | Ask for pictures |")
    r = parse_pasted_row(row)
    assert r.ok
    assert r.car["brand"] in ("VW", "Volkswagen")
    assert r.car["model"] == "Polo"
    assert r.car["year"] == 2014
    assert r.car["km"] == 280000
    assert r.car["price"] == 2300


# --------------------------------------------------------------------------- #
# 4. Row + header (header is optional but supported)
# --------------------------------------------------------------------------- #
def test_row_with_header():
    text = (
        "Year\tType\tBrand\tModel\tVariant\ttype\tVIN\tColour\tKm\tPrice full vat\n"
        "2014\tDiesel\tVW\tPolo\t1,4 TDI BMT Highline 90HK 5d\thatchback\t"
        "WVWZZZ6RZFY064440\tBlack\t280000\t2300"
    )
    r = parse_pasted_row(text)
    assert r.ok
    assert r.mode == "header"
    assert r.car["brand"] in ("VW", "Volkswagen")
    assert r.car["model"] == "Polo"
    assert r.car["year"] == 2014
    assert r.car["fuel"] == "Diesel"          # ambiguous "type" resolved by value
    assert r.car["body_type"] == "Hatchback"  # second "type" resolved by value
    assert r.car["km"] == 280000
    assert r.car["price"] == 2300


# --------------------------------------------------------------------------- #
# 5. The other known table format (Format A), with header
# --------------------------------------------------------------------------- #
def test_format_a_with_header():
    text = (
        "Registration\tFuel\tBrand\tModel\tVariant\tKar.\tVIN\tKm\tPrice\n"
        "10/07/2014\tDiesel\tVW\tPolo\t1,4 TDI BMT Highline 90HK 5d\tHatchback\t"
        "WVWZZZ6RZFY064440\t280000\t2300"
    )
    r = parse_pasted_row(text)
    assert r.ok
    assert r.mode == "header"
    assert r.car["year"] == 2014          # Registration date -> year
    assert r.car["fuel"] == "Diesel"
    assert r.car["body_type"] == "Hatchback"   # "Kar." -> body_type
    assert r.car["km"] == 280000
    assert r.car["price"] == 2300


# --------------------------------------------------------------------------- #
# 6. Missing optional fields (no body, no variant, no colour)
# --------------------------------------------------------------------------- #
def test_missing_optional_fields():
    row = "2018\tPetrol\tToyota\tYaris\t95000\t9500"
    r = parse_pasted_row(row)
    assert r.ok
    assert r.car["brand"] == "Toyota"
    assert r.car["model"] == "Yaris"
    assert r.car["year"] == 2018
    assert r.car["fuel"] == "Petrol"
    assert r.car["km"] == 95000
    assert r.car["price"] == 9500
    # optional fields simply absent, not errored
    assert r.car["body_type"] is None
    assert "body_type" not in r.not_detected  # body is optional, not "recommended"


# --------------------------------------------------------------------------- #
# 7. Ambiguous fields — a lone number cannot be split into km AND price
# --------------------------------------------------------------------------- #
def test_ambiguous_single_number():
    row = "2016\tDiesel\tBMW\t3 Series\t150000"
    r = parse_pasted_row(row)
    # brand+model+year+fuel detected; the single number is taken as km (order),
    # leaving price not detected and flagged for the user.
    assert r.ok
    assert r.car["brand"] == "BMW"
    assert "price" in r.not_detected
    assert r.fields["km"].confidence == "low"  # position-based, flagged


# --------------------------------------------------------------------------- #
# 8. Malformed / partial paste
# --------------------------------------------------------------------------- #
def test_malformed_partial():
    assert parse_pasted_row("").mode == "empty"
    assert parse_pasted_row("   ").mode == "empty"
    r = parse_pasted_row("just some free text with no structure")
    # one plain token becomes model at best; brand missing -> not ok
    assert not r.ok
    assert "brand" in r.not_detected


def test_header_only_paste():
    text = "Year | Type | Brand | Model | Variant | VIN | Km | Price"
    r = parse_pasted_row(text)
    assert not r.ok
    assert r.mode == "header_only"
    assert any("header" in i.lower() for i in r.issues)


# --------------------------------------------------------------------------- #
# 9. SOLD / non-numeric price
# --------------------------------------------------------------------------- #
def test_sold_price():
    row = "2017\tDiesel\tAudi\tA4\t2.0 TDI\t130000\tSOLD"
    r = parse_pasted_row(row)
    assert r.ok                       # still a valid record
    assert r.car["price"] is None     # SOLD -> no numeric price
    assert "price" in r.not_detected
    assert any("sold" in i.lower() for i in r.issues)


# --------------------------------------------------------------------------- #
# 10. Different date formats all resolve to a year
# --------------------------------------------------------------------------- #
def test_various_date_formats():
    for datestr, expected in [
        ("10.7.2014", 2014),
        ("10/07/2014", 2014),
        ("2014-03-01", 2014),
        ("2019", 2019),
        ("01-12-2020", 2020),
    ]:
        row = f"{datestr}\tDiesel\tSkoda\tOctavia\t120000\t15000"
        r = parse_pasted_row(row)
        assert r.car["year"] == expected, f"{datestr} -> {r.car['year']}"


# --------------------------------------------------------------------------- #
# 11. Different fuel labels normalize to canonical categories
# --------------------------------------------------------------------------- #
def test_various_fuel_labels():
    for label, expected in [
        ("Nafta", "Diesel"),
        ("Benzin", "Petrol"),
        ("TDI", "Diesel"),
        ("Elektro", "Electric"),
        ("PHEV", "PHEV"),
        ("Mild hybrid", "Hybrid"),
    ]:
        row = f"2019\t{label}\tSkoda\tOctavia\t120000\t15000"
        r = parse_pasted_row(row)
        assert r.car["fuel"] == expected, f"{label} -> {r.car['fuel']}"


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
