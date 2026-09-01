"""Tests for the geographic grid generator (scanner.build_grid_cells)."""
from __future__ import annotations

import math

from scanner import build_grid_cells, count_search_jobs, km_per_degree_longitude

# Approximate Tirana bounds (same defaults as config.py).
MIN_LAT, MAX_LAT = 41.285, 41.370
MIN_LON, MAX_LON = 19.735, 19.900


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Ground distance between two points, in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def test_grid_centers_are_generated() -> None:
    cells = build_grid_cells(MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, 1.25)
    assert len(cells) > 1
    for cell in cells:
        assert 1 <= cell.index <= len(cells)
        assert cell.total == len(cells)
        assert cell.row >= 0 and cell.col >= 0


def test_grid_centers_respect_bounds() -> None:
    cells = build_grid_cells(MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, 1.25)
    for cell in cells:
        assert MIN_LAT <= cell.latitude <= MAX_LAT
        assert MIN_LON <= cell.longitude <= MAX_LON


def test_grid_centers_are_unique() -> None:
    cells = build_grid_cells(MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, 1.25)
    coords = {(c.latitude, c.longitude) for c in cells}
    assert len(coords) == len(cells)


def test_grid_size_affects_cell_count() -> None:
    small = build_grid_cells(MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, 1.25)
    large = build_grid_cells(MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, 5.0)
    assert len(small) > len(large)

    # A grid cell much bigger than the whole area -> a single cell.
    huge = build_grid_cells(MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, 100.0)
    assert len(huge) == 1
    assert huge[0].total == 1


def test_cells_are_roughly_square_on_the_ground() -> None:
    """Longitude steps must be converted with the local km-per-degree.

    If the grid added the same numeric amount to latitude and longitude,
    cells at Tirana's latitude would be noticeably narrower horizontally
    than vertically; a correct grid keeps them roughly equal.
    """
    cells = build_grid_cells(MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, 1.25)
    by_row: dict[int, dict[int, object]] = {}
    for cell in cells:
        by_row.setdefault(cell.row, {})[cell.col] = cell

    if len(by_row) < 2 or len(by_row[0]) < 2:
        return  # not enough cells to measure spacings

    vertical = _haversine_km(
        by_row[0][0].latitude, by_row[0][0].longitude,
        by_row[1][0].latitude, by_row[1][0].longitude,
    )
    horizontal = _haversine_km(
        by_row[0][0].latitude, by_row[0][0].longitude,
        by_row[0][1].latitude, by_row[0][1].longitude,
    )
    assert 0.8 <= horizontal / vertical <= 1.2


def test_km_per_degree_longitude_shrinks_with_latitude() -> None:
    at_tirana = km_per_degree_longitude(41.33)
    at_equator = km_per_degree_longitude(0.0)
    assert 80.0 < at_tirana < 85.0
    assert at_equator > at_tirana


def test_count_search_jobs() -> None:
    cells = build_grid_cells(MIN_LAT, MAX_LAT, MIN_LON, MAX_LON, 1.25)
    assert count_search_jobs(cells, ["cafe", "bar"]) == 2 * len(cells)
    assert count_search_jobs(cells, []) == 0
