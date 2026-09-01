"""End-to-end scan test with the Places API mocked out (no network).

Verifies the whole MVP pipeline in one shot:
grid -> search jobs -> normalization -> SQLite dedup -> scoring -> CSV/XLSX
export -> resumable re-run (no duplicates).
"""
from __future__ import annotations

import csv
import importlib.util
import os
import time

import scanner
from config import AppConfig
from database import Database

FAKE_PLACES = [
    # 100: 40 (412 reviews) + 25 (4.8) + 5 (phone) + 30 (no website)
    {"id": "fake-001", "displayName": {"text": "Kafei Panorama"},
     "formattedAddress": "Rruga e Kavajes 45, Tirane",
     "nationalPhoneNumber": "051 111 0001", "rating": 4.8,
     "userRatingCount": 412, "googleMapsUri": "https://maps.google.com/?cid=1",
     "location": {"latitude": 41.327, "longitude": 19.819},
     "types": ["cafe", "food"], "primaryType": "cafe"},
    # 55: 30 + 20 + 5 + 0 (website listed)
    {"id": "fake-002", "displayName": {"text": "Restoranti Muzgut"},
     "formattedAddress": "Sheshi Skanderbeg 3, Tirane",
     "nationalPhoneNumber": "068 111 2233", "rating": 4.6,
     "userRatingCount": 187, "websiteUri": "https://muzgut.al",
     "googleMapsUri": "https://maps.google.com/?cid=2",
     "location": {"latitude": 41.329, "longitude": 19.819},
     "types": ["restaurant", "food"], "primaryType": "restaurant"},
    # 80: 20 + 25 + 5 + 30
    {"id": "fake-003", "displayName": {"text": "Saloni Elegance"},
     "formattedAddress": "Rruga Hasan Prishtina 12, Tirane",
     "nationalPhoneNumber": "055 999 8877", "rating": 4.9,
     "userRatingCount": 95, "googleMapsUri": "https://maps.google.com/?cid=3",
     "location": {"latitude": 41.325, "longitude": 19.815},
     "types": ["beauty_salon"], "primaryType": "beauty_salon"},
    # 35: 20 + 10 + 5 + 0
    {"id": "fake-004", "displayName": {"text": "Barriera Bistro"},
     "formattedAddress": "Rruga e Durrit 88, Tirane",
     "nationalPhoneNumber": "052 444 3322", "rating": 4.4,
     "userRatingCount": 58, "websiteUri": "https://barriera.al",
     "googleMapsUri": "https://maps.google.com/?cid=4",
     "location": {"latitude": 41.330, "longitude": 19.822},
     "types": ["restaurant"], "primaryType": "restaurant"},
    # 65: 10 + 25 + 0 + 30
    {"id": "fake-005", "displayName": {"text": "Patiseri Miella"},
     "formattedAddress": "Blloku, Tirane", "rating": 4.7,
     "userRatingCount": 44, "googleMapsUri": "https://maps.google.com/?cid=5",
     "location": {"latitude": 41.329, "longitude": 19.820},
     "types": ["bakery", "food"], "primaryType": "bakery"},
    # 30: 0 + 0 + 0 + 30
    {"id": "fake-006", "displayName": {"text": "Piknik 99"},
     "formattedAddress": "Rruga Nene Tereza 99, Tirane",
     "userRatingCount": 10, "googleMapsUri": "https://maps.google.com/?cid=6",
     "location": {"latitude": 41.332, "longitude": 19.825},
     "types": ["cafe"], "primaryType": "cafe"},
]

EXPECTED_ORDER = ["fake-001", "fake-003", "fake-005", "fake-002", "fake-004", "fake-006"]
EXPECTED_SCORES = {"fake-001": 100, "fake-002": 55, "fake-003": 80,
                   "fake-004": 35, "fake-005": 65, "fake-006": 30}


class FakePlacesClient:
    """Mimics PlacesClient; every search returns an overlapping subset."""

    def __init__(self, api_key="", delay_s=0.0, max_retries=0,
                 rank_preference="DISTANCE", timeout_s=1.0):
        self.requests = 0

    def search(self, latitude, longitude, radius_m, included_types):
        self.requests += 1
        if len(included_types) > 1:  # batch mode: everything
            return list(FAKE_PLACES)
        if included_types == ["cafe"]:
            return list(FAKE_PLACES[0:4])
        return list(FAKE_PLACES[2:6])


def _config(tmp_path, no_website_only=False) -> AppConfig:
    return AppConfig(
        grid_size_km=5.0,
        categories=["cafe", "restaurant"],
        request_delay_s=0.0,
        no_website_only=no_website_only,
        database_path=str(tmp_path / "db.sqlite"),
        csv_path=str(tmp_path / "out.csv"),
        xlsx_path=str(tmp_path / "out.xlsx"),
        api_key="test-key-not-used",
    )


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_full_scan_pipeline_mocked(tmp_path, monkeypatch) -> None:
    fake = FakePlacesClient()
    monkeypatch.setattr(scanner, "PlacesClient", lambda **kw: fake)

    config = _config(tmp_path)
    code = scanner.run_scan(config, dry_run=False, quiet=True)
    assert code == 0
    # 6 cells (5 km grid over Tirana) x 2 categories = 12 search jobs.
    assert fake.requests == 12

    # 12 jobs return 6 unique businesses (overlaps are deduplicated).
    with Database(config.database_path) as db:
        stats = db.get_stats()
    assert stats["total"] == 6
    assert stats["without_website"] == 4
    assert stats["with_website"] == 2

    # CSV exists, sorted by prospect_score DESC, review_count DESC.
    rows = _read_csv(config.csv_path)
    assert [r["place_id"] for r in rows] == EXPECTED_ORDER
    for row in rows:
        assert int(row["prospect_score"]) == EXPECTED_SCORES[row["place_id"]]
    no_website = [r["place_id"] for r in rows
                  if r["has_google_maps_website"] == "false"]
    assert no_website == ["fake-001", "fake-003", "fake-005", "fake-006"]

    # XLSX is produced when openpyxl is installed.
    if importlib.util.find_spec("openpyxl") is not None:
        assert os.path.exists(config.xlsx_path)

    # Re-run: dedup by Place ID keeps the total stable.
    time.sleep(1.1)  # let discovered_at differ, to prove it is preserved
    with Database(config.database_path) as db:
        first_seen = db.conn.execute(
            "SELECT discovered_at FROM businesses WHERE place_id = 'fake-001'"
        ).fetchone()["discovered_at"]

    code = scanner.run_scan(config, dry_run=False, quiet=True)
    assert code == 0
    with Database(config.database_path) as db:
        assert db.get_stats()["total"] == 6
        still = db.conn.execute(
            "SELECT discovered_at FROM businesses WHERE place_id = 'fake-001'"
        ).fetchone()["discovered_at"]
    assert still == first_seen


def test_batch_types_mode_mocked(tmp_path, monkeypatch) -> None:
    """--batch-types: one request per cell (all categories combined)."""
    fake = FakePlacesClient()
    monkeypatch.setattr(scanner, "PlacesClient", lambda **kw: fake)

    config = _config(tmp_path)
    config.batch_types = True
    code = scanner.run_scan(config, dry_run=False, quiet=True)
    assert code == 0
    # 6 cells x ONE combined request = 6 API requests (not 12).
    assert fake.requests == 6
    with Database(config.database_path) as db:
        assert db.get_stats()["total"] == len(FAKE_PLACES)


def test_no_website_only_export_mocked(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(scanner, "PlacesClient", lambda **kw: FakePlacesClient(**kw))

    config = _config(tmp_path, no_website_only=True)
    code = scanner.run_scan(config, dry_run=False, quiet=True)
    assert code == 0

    rows = _read_csv(config.csv_path)
    assert [r["place_id"] for r in rows] == ["fake-001", "fake-003", "fake-005", "fake-006"]
    assert all(r["has_google_maps_website"] == "false" for r in rows)


def test_dry_run_makes_no_requests(tmp_path, monkeypatch, capsys) -> None:
    def explode(**_kw):
        raise AssertionError("dry run must not construct the API client")

    monkeypatch.setattr(scanner, "PlacesClient", explode)
    config = _config(tmp_path)
    code = scanner.run_scan(config, dry_run=True)
    assert code == 0
    out = capsys.readouterr().out
    assert "No API requests will be made." in out
    assert "Grid cells:" in out and "Search jobs:" in out
