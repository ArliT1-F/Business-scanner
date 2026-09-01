"""SQLite persistence for discovered businesses.

Deduplication rule: the Google Place ID is the PRIMARY KEY. When the same
Place ID is seen again (overlapping grid cells, overlapping categories,
repeat scans) the existing row is updated in place - never duplicated.
We never deduplicate by business name, because different businesses can
share the same name.

The table matches the schema requested in the project specification.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
    place_id                 TEXT PRIMARY KEY,
    name                     TEXT NOT NULL DEFAULT '',
    address                  TEXT NOT NULL DEFAULT '',
    phone                    TEXT NOT NULL DEFAULT '',
    rating                   REAL,
    review_count             INTEGER NOT NULL DEFAULT 0,
    website                  TEXT NOT NULL DEFAULT '',
    maps_uri                 TEXT NOT NULL DEFAULT '',
    latitude                 REAL,
    longitude                REAL,
    primary_type             TEXT,
    types                    TEXT NOT NULL DEFAULT '',
    has_google_maps_website  INTEGER NOT NULL DEFAULT 0,
    prospect_score           INTEGER NOT NULL DEFAULT 0,
    discovered_at            TEXT
);

CREATE INDEX IF NOT EXISTS idx_businesses_has_website
    ON businesses(has_google_maps_website);
CREATE INDEX IF NOT EXISTS idx_businesses_prospect_score
    ON businesses(prospect_score);
CREATE INDEX IF NOT EXISTS idx_businesses_review_count
    ON businesses(review_count);
"""


class Database:
    """Thin wrapper around the SQLite connection with the app's queries."""

    def __init__(self, path: str) -> None:
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writes --------------------------------------------------------------
    def upsert_business(self, record: Dict[str, Any]) -> bool:
        """Insert a normalized record, or update the existing Place ID.

        Returns True when a NEW row was created, False when an existing row
        was updated. The prospect score is (re)computed from the MERGED row
        so that a partial update cannot silently corrupt it.
        """
        from scoring import compute_prospect_score  # local import: no cycle

        place_id = record["place_id"]
        row = self.conn.execute(
            "SELECT place_id, name, address, phone, rating, review_count,"
            " website, maps_uri, latitude, longitude, primary_type, types"
            " FROM businesses WHERE place_id = ?",
            (place_id,),
        ).fetchone()

        if row is None:
            score = compute_prospect_score(
                review_count=record.get("review_count"),
                rating=record.get("rating"),
                phone=record.get("phone"),
                website=record.get("website"),
            )
            self.conn.execute(
                "INSERT INTO businesses ("
                " place_id, name, address, phone, rating, review_count,"
                " website, maps_uri, latitude, longitude, primary_type, types,"
                " has_google_maps_website, prospect_score, discovered_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    place_id,
                    record.get("name") or "",
                    record.get("address") or "",
                    record.get("phone") or "",
                    record.get("rating"),
                    record.get("review_count") or 0,
                    record.get("website") or "",
                    record.get("maps_uri") or "",
                    record.get("latitude"),
                    record.get("longitude"),
                    record.get("primary_type") or None,
                    record.get("types") or "",
                    1 if record.get("website") else 0,
                    score,
                    record.get("discovered_at")
                    or _now_iso(),
                ),
            )
            self.conn.commit()
            return True

        # Existing Place ID: merge. Non-empty new values win; empty new
        # values keep the previously stored ones.
        merged = {
            "name": _pick(record.get("name"), row["name"]),
            "address": _pick(record.get("address"), row["address"]),
            "phone": _pick(record.get("phone"), row["phone"]),
            "rating": record.get("rating") if record.get("rating") is not None else row["rating"],
            "review_count": max(int(row["review_count"] or 0), int(record.get("review_count") or 0)),
            "website": _pick(record.get("website"), row["website"]),
            "maps_uri": _pick(record.get("maps_uri"), row["maps_uri"]),
            "latitude": record.get("latitude") if record.get("latitude") is not None else row["latitude"],
            "longitude": record.get("longitude") if record.get("longitude") is not None else row["longitude"],
            "primary_type": _pick(record.get("primary_type"), row["primary_type"]),
            "types": _pick(record.get("types"), row["types"]),
        }
        # A business that previously had no listed website and now does (or
        # vice versa) - the latest observed state wins for this flag.
        has_website = 1 if (merged["website"]) else 0

        score = compute_prospect_score(
            review_count=merged["review_count"],
            rating=merged["rating"],
            phone=merged["phone"],
            website=merged["website"],
        )

        self.conn.execute(
            "UPDATE businesses SET"
            " name = ?, address = ?, phone = ?, rating = ?,"
            " review_count = ?, website = ?, maps_uri = ?,"
            " latitude = ?, longitude = ?, primary_type = ?, types = ?,"
            " has_google_maps_website = ?, prospect_score = ?"
            " WHERE place_id = ?",
            (
                merged["name"],
                merged["address"],
                merged["phone"],
                merged["rating"],
                merged["review_count"],
                merged["website"],
                merged["maps_uri"],
                merged["latitude"],
                merged["longitude"],
                merged["primary_type"],
                merged["types"],
                has_website,
                score,
                place_id,
            ),
        )
        self.conn.commit()
        return False

    # -- reads ---------------------------------------------------------------
    def get_stats(self) -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total,"
            " COALESCE(SUM(has_google_maps_website), 0) AS with_website,"
            " COALESCE(AVG(rating), NULL) AS avg_rating"
            " FROM businesses"
        ).fetchone()
        total = int(row["total"] or 0)
        with_website = int(row["with_website"] or 0)
        return {
            "total": total,
            "with_website": with_website,
            "without_website": total - with_website,
            "avg_rating": row["avg_rating"],
        }

    def top_categories(self, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT primary_type, COUNT(*) AS n FROM businesses"
            " WHERE primary_type IS NOT NULL AND primary_type != ''"
            " GROUP BY primary_type ORDER BY n DESC, primary_type ASC"
            " LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"type": r["primary_type"], "count": int(r["n"])} for r in rows]

    def top_prospects(self, limit: int = 5) -> List[sqlite3.Row]:
        """Best website-less prospects, for the `stats` command."""
        return self.conn.execute(
            "SELECT * FROM businesses"
            " WHERE has_google_maps_website = 0"
            " ORDER BY prospect_score DESC, review_count DESC,"
            " name COLLATE NOCASE ASC"
            " LIMIT ?",
            (limit,),
        ).fetchall()

    def fetch_for_export(self, no_website_only: bool = False) -> List[sqlite3.Row]:
        sql = "SELECT * FROM businesses"
        params: tuple = ()
        if no_website_only:
            sql += " WHERE has_google_maps_website = 0"
        sql += (
            " ORDER BY prospect_score DESC,"
            " review_count DESC,"
            " name COLLATE NOCASE ASC"
        )
        return self.conn.execute(sql, params).fetchall()


def _pick(new_value: Optional[str], old_value: Optional[str]) -> str:
    """Prefer the non-empty new value; fall back to the stored one."""
    new_value = (new_value or "").strip()
    return new_value if new_value else (old_value or "")


def _now_iso() -> str:
    from utils import now_iso  # local import keeps module import order simple

    return now_iso()