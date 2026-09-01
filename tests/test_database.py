"""Tests for the SQLite layer: deduplication by Place ID, merging, scoring."""
from __future__ import annotations

from database import Database


def _record(**overrides) -> dict:
    rec = {
        "place_id": "ChIJtest",
        "name": "Test Business",
        "address": "Rruga 1, Tirane",
        "phone": "",
        "rating": None,
        "review_count": 0,
        "website": "",
        "maps_uri": "https://maps.google.com/?cid=1",
        "latitude": 41.3,
        "longitude": 19.8,
        "primary_type": "cafe",
        "types": "cafe,food",
        "discovered_at": "2026-01-01T00:00:00Z",
    }
    rec.update(overrides)
    return rec


def test_insert_then_same_place_id_is_updated_not_duplicated(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    try:
        assert db.upsert_business(_record()) is True    # new row
        assert db.upsert_business(_record()) is False   # same Place ID
        assert db.get_stats()["total"] == 1
    finally:
        db.close()


def test_update_merges_fields_and_keeps_discovered_at(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    try:
        db.upsert_business(_record(phone="051 111", review_count=10, rating=4.0))
        # Second sighting: phone/rating missing, more reviews.
        db.upsert_business(_record(phone="", rating=None, review_count=25))
        row = db.conn.execute(
            "SELECT * FROM businesses WHERE place_id = ?", ("ChIJtest",)
        ).fetchone()
        assert row["phone"] == "051 111"        # empty new value keeps the old one
        assert row["rating"] == 4.0             # None new value keeps the old one
        assert row["review_count"] == 25        # higher review count wins
        assert row["discovered_at"] == "2026-01-01T00:00:00Z"
    finally:
        db.close()


def test_different_place_ids_are_not_merged_by_name(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    try:
        db.upsert_business(_record(place_id="ChIJone"))
        db.upsert_business(_record(place_id="ChIJtwo"))  # same name, new id
        assert db.get_stats()["total"] == 2
    finally:
        db.close()


def test_prospect_score_is_computed_and_refreshed(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    try:
        db.upsert_business(_record())  # no reviews/phone, no website -> 30
        row = db.conn.execute("SELECT prospect_score FROM businesses").fetchone()
        assert row["prospect_score"] == 30

        db.upsert_business(
            _record(review_count=300, rating=4.8, phone="051", website="https://x.al")
        )
        row = db.conn.execute(
            "SELECT prospect_score, has_google_maps_website FROM businesses"
        ).fetchone()
        # 40 (reviews) + 25 (rating) + 5 (phone) + 0 (website now listed)
        assert row["prospect_score"] == 70
        assert row["has_google_maps_website"] == 1
    finally:
        db.close()


def test_export_ordering_and_no_website_filter(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    try:
        db.upsert_business(
            _record(place_id="A", name="Low", review_count=10, website="https://a.al")
        )
        db.upsert_business(
            _record(place_id="B", name="High", review_count=500, website="")
        )
        all_rows = db.fetch_for_export()
        assert [r["place_id"] for r in all_rows] == ["B", "A"]

        only_no_site = db.fetch_for_export(no_website_only=True)
        assert [r["place_id"] for r in only_no_site] == ["B"]
    finally:
        db.close()
