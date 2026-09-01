"""Central configuration for the Tirana Business Finder.

Every tunable lives here (or is overridden on the command line in main.py).
Nothing else in the project should hard-code geographic bounds, category
lists, delays, retry counts, or file paths.

The Google API key is read from the environment / .env file (python-dotenv)
and is never hard-coded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

# Load .env from the project root (if present) into os.environ.
load_dotenv()


# ---------------------------------------------------------------------------
# Geographic coverage (approximate Tirana city bounds)
# ---------------------------------------------------------------------------
CITY = "Tirana, Albania"
MIN_LAT = 41.285
MAX_LAT = 41.370
MIN_LON = 19.735
MAX_LON = 19.900

# Geographic grid cell size, in kilometres (on the ground, not in degrees).
GRID_SIZE_KM = 1.25

# Radius of each Nearby Search request, in metres.
# Google's Nearby Search accepts 100 .. 50000.
SEARCH_RADIUS_METERS = 900

# ---------------------------------------------------------------------------
# Business categories (Google Places API (New) feature types)
# ---------------------------------------------------------------------------
DEFAULT_CATEGORIES: List[str] = [
    # Food & drink
    "restaurant",
    "cafe",
    "bar",
    "bakery",
    "meal_takeaway",
    "food",
    # Retail
    "clothing_store",
    "shoe_store",
    "jewelry_store",
    "electronics_store",
    "furniture_store",
    "home_goods_store",
    "hardware_store",
    "supermarket",
    "convenience_store",
    "florist",
    "pet_store",
    "book_store",
    # Beauty & health
    "beauty_salon",
    "hair_care",
    "barber_shop",
    "spa",
    "gym",
    "pharmacy",
    "dentist",
    "doctor",
    "veterinary_care",
    # Automotive
    "car_repair",
    "car_dealer",
    "car_wash",
    # Services & professional
    "travel_agency",
    "real_estate_agency",
    "insurance_agency",
    "accounting",
    "lawyer",
    # Accommodation
    "lodging",
]

# ---------------------------------------------------------------------------
# API behaviour
# ---------------------------------------------------------------------------
# Minimum delay between consecutive API requests (seconds).
DEFAULT_REQUEST_DELAY_S = 0.5
# Number of retries after the initial attempt for transient errors
# (429/500/502/503/504). Backoff is 1s, 2s, 4s, ... (exponential).
DEFAULT_MAX_RETRIES = 3
# Nearby Search (New) returns at most this many results per request.
# The method has NO pagination (no pageToken), so this is a hard limit
# per search job; we respect it instead of trying to work around it.
MAX_RESULTS_PER_SEARCH = 20
# Maximum number of feature types accepted in one request (Google limit).
MAX_TYPES_PER_REQUEST = 50
# Nearby Search ranking preference. DISTANCE helps a grid scan discover
# different businesses in overlapping cells instead of returning the same
# popular results everywhere.
RANK_PREFERENCE = "DISTANCE"
# HTTP timeout for a single API request, in seconds.
REQUEST_TIMEOUT_S = 30.0

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
DATA_DIR = "data"
OUTPUT_DIR = "output"
DATABASE_PATH = os.path.join(DATA_DIR, "tirana_businesses.db")
CSV_PATH = os.path.join(OUTPUT_DIR, "tirana_businesses.csv")
XLSX_PATH = os.path.join(OUTPUT_DIR, "tirana_businesses.xlsx")


@dataclass
class AppConfig:
    """Runtime configuration. CLI flags override these defaults."""

    city: str = CITY
    min_lat: float = MIN_LAT
    max_lat: float = MAX_LAT
    min_lon: float = MIN_LON
    max_lon: float = MAX_LON
    grid_size_km: float = GRID_SIZE_KM
    search_radius_m: int = SEARCH_RADIUS_METERS
    categories: List[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    # False (default, per the project spec): one API request per
    # (cell, category). True: ONE request per cell carrying all
    # categories in `includedTypes` (up to Google's 50-type limit) -
    # roughly len(categories) times fewer API requests.
    batch_types: bool = False
    request_delay_s: float = DEFAULT_REQUEST_DELAY_S
    max_retries: int = DEFAULT_MAX_RETRIES
    rank_preference: str = RANK_PREFERENCE
    request_timeout_s: float = REQUEST_TIMEOUT_S
    # When True, the final export contains only businesses without a
    # website listed on Google Maps.
    no_website_only: bool = False
    database_path: str = DATABASE_PATH
    csv_path: str = CSV_PATH
    xlsx_path: str = XLSX_PATH
    api_key: str = field(
        default_factory=lambda: os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    )

    def __post_init__(self) -> None:
        # Normalise the category list (drop empty entries, de-duplicate,
        # keep order).
        seen = set()
        cleaned: List[str] = []
        for cat in self.categories:
            cat = cat.strip()
            if cat and cat not in seen:
                seen.add(cat)
                cleaned.append(cat)
        self.categories = cleaned
        self.validate()

    def validate(self) -> None:
        """Raise ValueError with a readable message if the config is unusable."""
        if not (self.min_lat < self.max_lat):
            raise ValueError("min_lat must be smaller than max_lat")
        if not (self.min_lon < self.max_lon):
            raise ValueError("min_lon must be smaller than max_lon")
        if not (-90.0 <= self.min_lat <= 90.0 and -90.0 <= self.max_lat <= 90.0):
            raise ValueError("latitude bounds must be within -90..90")
        if not (-180.0 <= self.min_lon <= 180.0 and -180.0 <= self.max_lon <= 180.0):
            raise ValueError("longitude bounds must be within -180..180")
        if self.grid_size_km <= 0:
            raise ValueError("grid_size_km must be positive")
        if not (100 <= self.search_radius_m <= 50000):
            raise ValueError(
                "search_radius_m must be between 100 and 50000 "
                "(Google Nearby Search limits)"
            )
        if not self.categories:
            raise ValueError("at least one category is required")
        if self.request_delay_s < 0:
            raise ValueError("request_delay_s must be >= 0")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
