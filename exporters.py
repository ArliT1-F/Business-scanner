"""CSV (and optional XLSX) export of the businesses database.

CSV is always available (standard library only). XLSX export requires the
optional `openpyxl` package; when it is missing the export simply skips
the XLSX step with a friendly notice instead of failing.

Rows are sorted by prospect_score DESC, then review_count DESC (and name,
for stable output), as required by the specification.
"""
from __future__ import annotations

import csv
import os
from typing import Any, Dict, List, Optional

from database import Database

CSV_COLUMNS = [
    "place_id",
    "name",
    "address",
    "phone",
    "rating",
    "review_count",
    "website",
    "maps_uri",
    "latitude",
    "longitude",
    "primary_type",
    "types",
    "has_google_maps_website",
    "prospect_score",
]


def _row_values(row) -> List[Any]:
    """Map one database row to the CSV column order."""
    return [
        row["place_id"],
        row["name"],
        row["address"],
        row["phone"],
        "" if row["rating"] is None else row["rating"],
        row["review_count"],
        row["website"] or "",
        row["maps_uri"] or "",
        "" if row["latitude"] is None else row["latitude"],
        "" if row["longitude"] is None else row["longitude"],
        row["primary_type"] or "",
        row["types"] or "",
        "true" if row["has_google_maps_website"] else "false",
        row["prospect_score"],
    ]


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def export_csv(rows: list, path: str) -> int:
    """Write rows to a CSV file. Returns the number of data rows."""
    _ensure_parent(path)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(_row_values(row))
    return len(rows)


def export_xlsx(rows: list, path: str, stats: Dict[str, Any], top_categories: list) -> int:
    """Write rows to an XLSX workbook (Businesses + Statistics sheets).

    Returns the number of data rows, or None when openpyxl is not installed
    (in which case nothing is written and a notice is printed).
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
    except ImportError:
        print(
            "XLSX export skipped: openpyxl is not installed. "
            "Install it with: pip install openpyxl"
        )
        return None

    _ensure_parent(path)
    wb = Workbook()
    ws = wb.active
    ws.title = "Businesses"
    ws.append(CSV_COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(_row_values(row))
    # Reasonable column widths (capped).
    for idx, column in enumerate(CSV_COLUMNS, start=1):
        longest = len(column)
        for row in rows[:200]:
            value = _row_values(row)[idx - 1]
            if value is not None:
                longest = max(longest, min(len(str(value)), 60))
        ws.column_dimensions[get_column_letter(idx)].width = min(longest + 2, 62)
    ws.freeze_panes = "A2"

    if stats:
        s = wb.create_sheet("Statistics")
        s.append(["Total businesses", stats.get("total", 0)])
        s.append(["Businesses with websites", stats.get("with_website", 0)])
        s.append(["Businesses without websites", stats.get("without_website", 0)])
        avg = stats.get("avg_rating")
        s.append(["Average rating", "" if avg is None else round(avg, 2)])
        s.append([])
        s.append(["Top categories"])
        for entry in top_categories:
            s.append([entry["type"], entry["count"]])
        s["A1"].font = Font(bold=True)
        s["A6"].font = Font(bold=True)

    wb.save(path)
    return len(rows)


def export_database(
    db: Database,
    csv_path: str,
    xlsx_path: str,
    no_website_only: bool = False,
) -> Dict[str, Optional[str]]:
    """Export the database to CSV (always) and XLSX (if openpyxl present).

    Returns {"csv": path or None, "xlsx": path or None}.
    """
    rows = db.fetch_for_export(no_website_only=no_website_only)
    if not rows:
        print(
            "Nothing to export (no businesses match the filter). "
            "Run a scan first."
        )
        return {"csv": None, "xlsx": None}

    stats = db.get_stats()
    top_categories = db.top_categories(10)

    csv_written = export_csv(rows, csv_path)
    scope = "without website" if no_website_only else "total"
    print(f"Exported {csv_written} businesses ({scope}) -> {csv_path}")

    xlsx_written = export_xlsx(rows, xlsx_path, stats, top_categories)
    result: Dict[str, Optional[str]] = {
        "csv": csv_path,
        "xlsx": xlsx_path if xlsx_written is not None else None,
    }
    return result