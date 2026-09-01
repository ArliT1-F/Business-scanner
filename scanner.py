"""Grid-based scanning over the Google Places API (New).

A single search query cannot discover every business in a city, so the
target area is divided into a geographic grid and every cell is searched
for every configured category.

The grid is computed in *kilometres on the ground*, not in raw degrees:
one degree of latitude and one degree of longitude cover different
distances, so the longitude step is derived from the area's mid-latitude.
The grid is only meant to improve discovery - Google's per-search result
cap still applies and is respected (see places.MAX_PAGES_PER_SEARCH).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from config import MAX_PAGES_PER_SEARCH, PAGE_SIZE, AppConfig
from database import Database
from exporters import export_database
from places import (
    PlacesAuthError,
    PlacesClient,
    PlacesError,
    PlacesQuotaError,
    missing_key_error,
    normalize_place,
)
from utils import fmt_int, now_iso, section


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------
# Mean ground distance covered by one degree of latitude / longitude.
KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON_AT_EQUATOR = 111.320


@dataclass(frozen=True)
class GridCell:
    index: int  # 1-based, across the whole grid
    total: int  # total number of cells
    row: int  # 0-based row
    col: int  # 0-based column
    latitude: float
    longitude: float


def km_per_degree_longitude(latitude: float) -> float:
    """Ground distance of 1 degree of longitude at a given latitude (km)."""
    return KM_PER_DEG_LON_AT_EQUATOR * math.cos(math.radians(latitude))


def build_grid_cells(
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    grid_size_km: float,
) -> List[GridCell]:
    """Tile the bounding box with roughly square cells of `grid_size_km`.

    Returns cell centers, row by row (south to north, west to east).
    Longitude spans are converted to kilometres using the mid-latitude,
    so cells are not stretched sideways at mid latitudes.
    """
    if grid_size_km <= 0:
        raise ValueError("grid_size_km must be positive")

    mid_lat = (min_lat + max_lat) / 2.0
    lat_span_km = (max_lat - min_lat) * KM_PER_DEG_LAT
    lon_span_km = (max_lon - min_lon) * km_per_degree_longitude(mid_lat)

    rows = max(1, math.ceil(lat_span_km / grid_size_km))
    cols = max(1, math.ceil(lon_span_km / grid_size_km))

    cells: List[GridCell] = []
    total = rows * cols
    index = 0
    for i in range(rows):
        lat = min_lat + (i + 0.5) * (max_lat - min_lat) / rows
        for j in range(cols):
            index += 1
            lon = min_lon + (j + 0.5) * (max_lon - min_lon) / cols
            cells.append(GridCell(index, total, i, j, round(lat, 6), round(lon, 6)))
    return cells


def count_search_jobs(cells: List[GridCell], categories: List[str]) -> int:
    """Number of API search jobs = cells * categories (before pagination)."""
    return len(cells) * len(categories)


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------
def print_dry_run_report(
    config: AppConfig,
    cells: List[GridCell],
    categories: List[str],
    total_jobs: int,
) -> None:
    print(f"Target: {config.city}")
    print(f"Grid cells:       {len(cells)}")
    print(f"Categories:       {len(categories)}")
    print(f"Search jobs:      {total_jobs}")
    print(f"Radius per search: {config.search_radius_m}m")
    print(
        f"Results per job:  up to {MAX_PAGES_PER_SEARCH} x {PAGE_SIZE} "
        "(Google's documented maximum)"
    )
    print(
        f"Estimated requests: {total_jobs} - {total_jobs * MAX_PAGES_PER_SEARCH}"
        f" (each job may use up to {MAX_PAGES_PER_SEARCH} pages)"
    )
    print()
    print("No API requests will be made.")


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------
def _print_progress_head(
    cell: GridCell,
    category: str,
    category_no: int,
    categories: List[str],
    radius_m: int,
) -> None:
    """Per-job console header in the project's documented format."""
    print(f"[{cell.index}/{cell.total}] Cell {cell.index}")
    print(f"[{category_no}/{len(categories)}] Category: {category}")
    print("Searching:")
    print(f"    latitude: {cell.latitude:.5f}")
    print(f"    longitude: {cell.longitude:.5f}")
    print(f"    radius: {radius_m}m")


def run_scan(
    config: AppConfig,
    dry_run: bool = False,
    limit: Optional[int] = None,
    quiet: bool = False,
) -> int:
    """Run the full scan (or a dry run). Returns a process exit code."""
    cells = build_grid_cells(
        config.min_lat,
        config.max_lat,
        config.min_lon,
        config.max_lon,
        config.grid_size_km,
    )
    categories = config.categories
    total_jobs = count_search_jobs(cells, categories)

    jobs = [(cell, cat) for cell in cells for cat in categories]
    if limit is not None and limit >= 0:
        jobs = jobs[:limit]

    # ------------------------------------------------------------ dry run
    if dry_run:
        print_dry_run_report(config, cells, categories, total_jobs)
        return 0

    # -------------------------------------------------- API key validation
    if not config.api_key:
        print(missing_key_error())
        return 1

    # ---------------------------------------------------------------- scan
    client = PlacesClient(
        api_key=config.api_key,
        delay_s=config.request_delay_s,
        max_retries=config.max_retries,
        rank_preference=config.rank_preference,
        timeout_s=config.request_timeout_s,
    )

    new_total = 0
    existing_total = 0
    searched_jobs = 0
    consecutive_errors = 0
    last_error_text = ""
    stop_reason: Optional[str] = None

    print(section(f"SCAN START - {config.city}", width=40).strip())
    print(
        f"Grid: {config.grid_size_km:g} km | radius: {config.search_radius_m} m "
        f"| jobs: {len(jobs)}"
    )
    print()

    try:
        with Database(config.database_path) as db:
            for cell, category in jobs:
                category_no = categories.index(category) + 1

                if not quiet:
                    _print_progress_head(
                        cell, category, category_no, categories,
                        config.search_radius_m,
                    )

                try:
                    returned = new = existing = 0
                    for raw in client.search_all_pages(
                        cell.latitude,
                        cell.longitude,
                        config.search_radius_m,
                        category,
                    ):
                        record = normalize_place(raw)
                        if record is None:
                            continue
                        record["discovered_at"] = now_iso()
                        if db.upsert_business(record):
                            new += 1
                        else:
                            existing += 1
                        returned += 1
                    searched_jobs += 1
                    new_total += new
                    existing_total += existing
                    consecutive_errors = 0

                    if not quiet:
                        stats = db.get_stats()
                        print("Results:")
                        print(f"    returned: {returned}")
                        print(f"    new: {new}")
                        print(f"    existing: {existing}")
                        print("Database:")
                        print(f"    total: {fmt_int(stats['total'])}")
                        print(
                            "    without website: "
                            f"{fmt_int(stats['without_website'])}"
                        )
                        print()
                except PlacesAuthError as exc:
                    print(f"\nFATAL: {exc}")
                    print(
                        "Fix the problem above and re-run; results stored so "
                        "far are kept."
                    )
                    stop_reason = "authentication/permission error"
                    break
                except PlacesQuotaError as exc:
                    print(f"\nFATAL: {exc}")
                    print(
                        "Stopping early to avoid further quota errors; results "
                        "stored so far are kept."
                    )
                    stop_reason = "quota exhausted"
                    break
                except PlacesError as exc:
                    consecutive_errors += 1
                    text = str(exc)
                    print(f"\nERROR (cell {cell.index}, {category}): {text}")
                    # A persistent request problem (e.g. an invalid place
                    # type) would waste time on every remaining job - stop
                    # after a few identical consecutive failures.
                    if consecutive_errors >= 3 and text == last_error_text:
                        print(
                            "The same error repeated 3 times in a row - "
                            "stopping the scan. Results stored so far are "
                            "kept."
                        )
                        stop_reason = "repeated search error"
                        break
                    last_error_text = text
                    print()

            # ------------------------------------------------------------ end
            stats = db.get_stats()

            if stats["total"] == 0:
                print("No results were stored.")
                return 1

            result = export_database(
                db,
                csv_path=config.csv_path,
                xlsx_path=config.xlsx_path,
                no_website_only=config.no_website_only,
            )
            csv_path = result.get("csv")
            xlsx_path = result.get("xlsx")

            print(section("SCAN COMPLETE", width=40).strip())
            print(f"Unique businesses:   {fmt_int(stats['total'])}")
            print(f"Without website:     {fmt_int(stats['without_website'])}")
            print(f"With website:        {fmt_int(stats['with_website'])}")
            if csv_path:
                print("CSV:")
                print(csv_path)
            if xlsx_path:
                print("XLSX:")
                print(xlsx_path)
            if stop_reason:
                print()
                print(
                    f"Note: stopped early ({stop_reason}) after "
                    f"{searched_jobs}/{len(jobs)} jobs. Re-run to continue; "
                    "known places are updated, not duplicated."
                )
            print()
            print(
                "Note: these are businesses discovered through the configured "
                "Google Places\nsearches and geographic/category coverage - "
                "not an exhaustive list of\nevery business in the area."
            )
            return 0
    except KeyboardInterrupt:
        print(
            "\nInterrupted. Progress is saved in the database; re-run to "
            "continue (known places are updated, not duplicated)."
        )
        return 130