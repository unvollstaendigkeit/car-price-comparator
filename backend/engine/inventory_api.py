"""
Inventory upload → normalization → validation report, as a JSON-serializable
contract for the frontend Inventory module.

This is a thin adapter. It does NOT contain any parsing or valuation logic of
its own — it:
  * reads an uploaded spreadsheet (.csv / .xlsx / .xls) into a DataFrame, and
  * defers entirely to `inventory_normalizer.normalize_inventory`, the SAME
    canonical normalizer used by the single-car paste path.

No marketplace scraping happens here. This phase stops at a reviewed,
confirmed inventory; the market analysis is a later phase.
"""

from __future__ import annotations

import io
import os
from typing import Optional

import pandas as pd

from inventory_normalizer import (
    CANONICAL_FIELDS,
    REQUIRED_FOR_COMPARISON,
    NormalizationReport,
    normalize_inventory,
)

# Bundled demo fixtures (copied into the deployed backend so the demo works in
# production with no external files). Keyed by the short name the API accepts.
_FIXTURE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")
DEMO_FIXTURES = {
    "sample": {
        "file": "inventory_sample.csv",
        "label": "Demo inventory (98 cars)",
    },
    "alt": {
        "file": "inventory_alt_format.csv",
        "label": "Alternative spreadsheet format",
    },
}

# Fields surfaced to the review table, in display order.
DISPLAY_FIELDS = [
    "brand", "model", "variant", "year", "fuel", "km", "price",
    "body_type", "vin", "colour", "status",
]


class InventoryReadError(Exception):
    """Raised when the uploaded file cannot be read into a table."""


# --------------------------------------------------------------------------- #
# File reading
# --------------------------------------------------------------------------- #
def read_table(filename: str, content: bytes) -> pd.DataFrame:
    """
    Read an uploaded spreadsheet into a DataFrame of strings, dispatching on the
    file extension. Everything is read as text so the normalizer sees the raw
    cell strings (it does all type coercion itself).
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if not content:
        raise InventoryReadError("The uploaded file is empty.")

    buf = io.BytesIO(content)
    try:
        if ext in (".csv", ".txt", ""):
            # utf-8 first, then a permissive fallback for Excel-exported CSVs.
            try:
                return pd.read_csv(buf, dtype=str, keep_default_na=False)
            except UnicodeDecodeError:
                buf.seek(0)
                return pd.read_csv(buf, dtype=str, keep_default_na=False, encoding="latin-1")
        if ext == ".xlsx":
            return pd.read_excel(buf, dtype=str, engine="openpyxl")
        if ext == ".xls":
            try:
                return pd.read_excel(buf, dtype=str, engine="xlrd")
            except ImportError as exc:
                raise InventoryReadError(
                    "Legacy .xls files aren't supported here. Please re-save as "
                    ".xlsx or .csv and upload again."
                ) from exc
        raise InventoryReadError(
            f"Unsupported file type '{ext or 'unknown'}'. Upload a .csv or .xlsx file."
        )
    except InventoryReadError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface a readable message to the UI
        raise InventoryReadError(f"Could not read the spreadsheet: {exc}") from exc


# --------------------------------------------------------------------------- #
# Report → JSON contract
# --------------------------------------------------------------------------- #
def _cell(v):
    """Native, JSON-safe scalar (NaN/NaT -> None)."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, str):
        return v.strip() or None
    # numpy scalar -> python scalar
    item = getattr(v, "item", None)
    if callable(item) and v.__class__.__module__ == "numpy":
        return v.item()
    return v


def _row_status(rec: dict) -> str:
    """
    Coarse, UI-facing status for the review table:
      "ready"  - valid for comparison
      "sold"   - a valid record whose price is SOLD/reserved/unavailable
      "review" - missing/invalid required field(s), needs attention
    """
    if rec.get("valid_for_comparison"):
        return "ready"
    if rec.get("status") in ("sold", "reserved", "unavailable"):
        return "sold"
    return "review"


def report_to_dict(report: NormalizationReport, source_name: str = "") -> dict:
    """Serialize a NormalizationReport into the frontend contract."""
    issues_by_row = {ri["row_index"]: list(ri["issues"]) for ri in report.row_issues}

    rows_out: list[dict] = []
    for _, r in report.rows.iterrows():
        rec = r.to_dict()
        idx = int(rec.get("row_index", 0))
        row = {f: _cell(rec.get(f)) for f in DISPLAY_FIELDS}
        row["row_index"] = idx
        row["row_number"] = idx + 1  # 1-based for display
        row["valid_for_comparison"] = bool(rec.get("valid_for_comparison"))
        row["status_label"] = _row_status(rec)
        row["issues"] = issues_by_row.get(idx, [])
        # keep the raw originals available for the details view
        row["price_original"] = _cell(rec.get("price_original"))
        row["km_original"] = _cell(rec.get("km_original"))
        row["year_source"] = _cell(rec.get("year_source"))
        rows_out.append(row)

    # Human-readable mapping (canonical field -> the source header it came from).
    mapping_display = [
        {"field": canon, "column": report.mapping[canon]}
        for canon in CANONICAL_FIELDS
        if canon in report.mapping
    ]

    counts = dict(report.counts)
    counts["review"] = max(
        counts.get("total_rows", 0)
        - counts.get("valid_for_comparison", 0)
        - counts.get("sold_or_unavailable", 0),
        0,
    )

    return {
        "ok": report.ok,
        "source_name": source_name,
        "counts": counts,
        "mapping": mapping_display,
        "unmapped_columns": list(report.unmapped_columns),
        "missing_required": list(report.missing_required),
        "ambiguities": list(report.ambiguities),
        "detected_columns": list(report.detected_columns),
        "required_fields": list(REQUIRED_FOR_COMPARISON),
        "rows": rows_out,
    }


# --------------------------------------------------------------------------- #
# Entry points used by the API layer
# --------------------------------------------------------------------------- #
def parse_upload(filename: str, content: bytes) -> dict:
    """Read an uploaded file and return the JSON validation report."""
    df = read_table(filename, content)
    report = normalize_inventory(df)
    return report_to_dict(report, source_name=filename or "uploaded file")


def parse_demo(name: str) -> dict:
    """Load a bundled demo fixture and return the JSON validation report."""
    fixture = DEMO_FIXTURES.get(name)
    if fixture is None:
        raise InventoryReadError(f"Unknown demo inventory '{name}'.")
    path = os.path.join(_FIXTURE_DIR, fixture["file"])
    if not os.path.exists(path):
        raise InventoryReadError(f"Demo inventory file is missing: {fixture['file']}.")
    report = normalize_inventory(path)
    return report_to_dict(report, source_name=fixture["label"])
