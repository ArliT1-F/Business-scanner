"""Client for the Google Places API (New) - official REST API only.

This module wraps the Nearby Search endpoint:

    POST https://places.googleapis.com/v1/places:searchNearby

Design rules:
  * The API key comes from the environment (.env via python-dotenv) and is
    NEVER printed or logged.
  * Only the fields the application actually needs are requested, via an
    HTTP field mask (X-Goog-FieldMask). No wildcard '*' masks.
  * Transient errors (429/500/502/503/504) are retried with exponential
    backoff (1s, 2s, 4s, ...) up to a configurable maximum. We do not retry
    forever and we do not hammer the servers.
  * Pagination follows Google's documented behaviour: a pageToken loop,
    capped at the documented maximum number of results per search.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests

from config import (
    MAX_PAGES_PER_SEARCH,
    PAGE_SIZE,
    RANK_PREFERENCE,
    REQUEST_TIMEOUT_S,
)

log = logging.getLogger(__name__)

NEARBY_SEARCH_URL = "https://places.googleapis.com/v1/places:searchNearby"

# Only the fields the application needs. Kept as small as possible -
# both for API quota/pricing and for clean data.
FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.nationalPhoneNumber,"
    "places.rating,"
    "places.userRatingCount,"
    "places.websiteUri,"
    "places.googleMapsUri,"
    "places.location,"
    "places.types,"
    "places.primaryType"
)

# HTTP status codes that are considered transient and worth retrying.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class PlacesError(Exception):
    """Base error with a user-facing message (safe to print)."""


class PlacesAuthError(PlacesError):
    """The API key is missing/invalid, or the API is not enabled."""


class PlacesQuotaError(PlacesError):
    """Google quota (per-day / per-month) appears to be exhausted."""


class PlacesApiError(PlacesError):
    """Non-retryable Places API error (bad request, disabled API, ...)."""


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def normalize_place(raw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Convert one raw Places API (New) place object into a flat record.

    Missing optional fields get safe defaults instead of crashing the scan:
        rating          -> None
        review_count    -> 0
        phone           -> ""
        website         -> ""
        address         -> ""
        location        -> None / None
    Returns None if the object carries no usable place id.
    """
    raw = raw or {}
    place_id = (raw.get("id") or "").strip()
    if not place_id:
        return None

    display_name = (raw.get("displayName") or {}).get("text") or ""
    address = raw.get("formattedAddress") or ""
    phone = raw.get("nationalPhoneNumber") or ""
    website = raw.get("websiteUri") or ""
    maps_uri = raw.get("googleMapsUri") or ""
    primary_type = raw.get("primaryType") or ""
    types = raw.get("types") or []
    if not isinstance(types, (list, tuple)):
        types = [str(types)]
    types_str = ",".join(str(t) for t in types if t)

    rating = raw.get("rating")
    if rating is not None:
        try:
            rating = float(rating)
        except (TypeError, ValueError):
            rating = None

    review_count = raw.get("userRatingCount") or 0
    try:
        review_count = int(review_count)
    except (TypeError, ValueError):
        review_count = 0

    location = raw.get("location") or {}
    latitude = location.get("latitude")
    longitude = location.get("longitude")

    return {
        "place_id": place_id,
        "name": display_name,
        "address": address,
        "phone": phone,
        "rating": rating,
        "review_count": review_count,
        # Central feature: "does Google list a website for this business?"
        # An empty websiteUri means "no website listed on Google Maps" -
        # NOT proof that the business has no website anywhere.
        "website": website,
        "has_google_maps_website": 1 if website else 0,
        "maps_uri": maps_uri,
        "latitude": latitude,
        "longitude": longitude,
        "primary_type": primary_type,
        "types": types_str,
    }


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class PlacesClient:
    """Small, defensive client for the Places API (New) Nearby Search."""

    def __init__(
        self,
        api_key: str,
        delay_s: float = 0.5,
        max_retries: int = 3,
        rank_preference: str = RANK_PREFERENCE,
        timeout_s: float = REQUEST_TIMEOUT_S,
    ) -> None:
        if not api_key:
            raise PlacesAuthError(_MISSING_KEY_MESSAGE)
        self.api_key = api_key
        self.delay_s = max(0.0, float(delay_s))
        self.max_retries = max(0, int(max_retries))
        self.rank_preference = rank_preference
        self.timeout_s = float(timeout_s)
        self._session = requests.Session()
        self._last_request_at = 0.0

    # -- public API ---------------------------------------------------------

    def search_nearby(
        self,
        latitude: float,
        longitude: float,
        radius_m: int,
        included_type: str,
        page_token: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Run one Nearby Search page.

        Returns (places, next_page_token). next_page_token is "" when there
        are no further pages.
        """
        payload: Dict[str, Any] = {
            "location": {"latitude": float(latitude), "longitude": float(longitude)},
            "radius": int(radius_m),
            "includedTypes": [included_type],
            "pageSize": PAGE_SIZE,
            "rankPreference": self.rank_preference,
        }
        if page_token:
            payload["pageToken"] = page_token

        data = self._post(payload)
        places = data.get("places") or []
        next_token = data.get("nextPageToken") or ""
        return places, next_token

    def search_all_pages(
        self,
        latitude: float,
        longitude: float,
        radius_m: int,
        included_type: str,
    ) -> Iterator[Dict[str, Any]]:
        """Yield every raw place from a search, following pageTokens.

        Pagination stops when Google stops offering a next page OR when the
        documented result maximum (MAX_PAGES_PER_SEARCH pages) is reached.
        """
        page_token: Optional[str] = None
        for _page in range(MAX_PAGES_PER_SEARCH):
            places, page_token = self.search_nearby(
                latitude, longitude, radius_m, included_type, page_token
            )
            yield from places
            if not page_token or not places:
                return
        # Reached Google's documented cap; do not try to go beyond it.
        log.debug(
            "Reached documented maximum of %d pages for (%s, %s); stopping.",
            MAX_PAGES_PER_SEARCH,
            included_type,
            (latitude, longitude),
        )

    # -- internals ------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        }

    def _throttle(self) -> None:
        """Enforce the configured minimum delay between requests."""
        if self.delay_s <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay_s:
            time.sleep(self.delay_s - elapsed)

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST with exponential backoff on transient errors."""
        backoff_s = 1.0
        last_error: Optional[str] = None

        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                response = self._session.post(
                    NEARBY_SEARCH_URL,
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout_s,
                )
            except requests.RequestException as exc:
                self._last_request_at = time.monotonic()
                last_error = (
                    "Network error while contacting the Google Places API: "
                    f"{exc.__class__.__name__}"
                )
                if attempt >= self.max_retries:
                    raise PlacesError(
                        f"ERROR: {last_error} "
                        f"(after {self.max_retries + 1} attempts)."
                    ) from exc
                log.warning(
                    "Network error (attempt %d/%d), retrying in %.0fs: %s",
                    attempt + 1,
                    self.max_retries + 1,
                    backoff_s,
                    exc,
                )
                time.sleep(backoff_s)
                backoff_s *= 2
                continue

            self._last_request_at = time.monotonic()

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise PlacesError(
                        "ERROR: Google returned an unparseable (non-JSON) "
                        "response."
                    ) from exc

            if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                log.warning(
                    "HTTP %d (attempt %d/%d), retrying in %.0fs",
                    response.status_code,
                    attempt + 1,
                    self.max_retries + 1,
                    backoff_s,
                )
                time.sleep(backoff_s)
                backoff_s *= 2
                continue

            raise self._build_api_error(response)

        raise PlacesError(last_error or "ERROR: The API request failed.")

    @staticmethod
    def _build_api_error(response: requests.Response) -> PlacesError:
        """Map a non-200 response to a specific, user-friendly error."""
        try:
            body = response.json()
        except ValueError:
            body = {}
        error = body.get("error") or {}
        grpc_code = error.get("code", "")
        status = str(error.get("status") or "")
        message = str(error.get("message") or "").strip()
        low = message.lower()

        # Missing / invalid / restricted API key. (401 = unauthenticated;
        # 403 is handled below, because it also covers "API disabled".)
        if (
            response.status_code == 401
            or "api key not valid" in low
            or "api_key_invalid" in low
            or "invalid api key" in low
        ):
            return PlacesAuthError(
                "ERROR: Google rejected the API key.\n"
                f"Google said: {message or '(no message)'}\n"
                "Check GOOGLE_MAPS_API_KEY in your .env file and the key's "
                "restrictions in Cloud Console."
            )

        # Places API (New) not enabled / no permission.
        if "has not been used" in low or "or it is disabled" in low:
            return PlacesAuthError(
                "ERROR: Google rejected the request.\n"
                "Check that Places API (New) is enabled and the API key "
                "has permission.\n"
                "Enable it at: https://console.cloud.google.com/apis/library/places-backend.googleapis.com"
            )
        if status == "PERMISSION_DENIED" or "permission" in low:
            return PlacesAuthError(
                "ERROR: Google rejected the request.\n"
                "Check that Places API (New) is enabled and the API key "
                "has permission.\n"
                f"Google said: {message}"
            )

        # Quota (per-day / per-month) vs. QPS rate limit - both use 429.
        if response.status_code == 429 or status == "RESOURCE_EXHAUSTED":
            if "rate" in low or "qps" in low:
                return PlacesQuotaError(
                    "ERROR: Google's per-second rate limit (QPS) was exceeded.\n"
                    "Increase --delay (e.g. --delay 2) and re-run; results "
                    "already stored in the database are kept."
                )
            return PlacesQuotaError(
                "ERROR: Google API quota appears to have been exceeded.\n"
                f"Google said: {message}\n"
                "Check the current limits in Cloud Console and on the "
                "Places API usage & billing page."
            )

        # Generic API error.
        detail = f"\nGoogle said: {message}" if message else ""
        return PlacesApiError(
            f"ERROR: Google Places API error "
            f"(HTTP {response.status_code}, code {grpc_code or 'unknown'}).{detail}\n"
            "Check that Places API (New) is enabled and the API key has "
            "permission, and that the requested place types are valid."
        )


_MISSING_KEY_MESSAGE = (
    "ERROR: GOOGLE_MAPS_API_KEY is missing.\n"
    "Create a .env file and add:\n"
    "GOOGLE_MAPS_API_KEY=..."
)


def missing_key_error() -> PlacesAuthError:
    """The exact user-facing error for a missing/empty API key."""
    return PlacesAuthError(_MISSING_KEY_MESSAGE)