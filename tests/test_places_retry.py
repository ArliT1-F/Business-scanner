"""Tests for the Places client: retries, backoff, error mapping (no network)."""
from __future__ import annotations

import requests
import pytest

from places import (
    FIELD_MASK,
    PlacesAuthError,
    PlacesClient,
    PlacesQuotaError,
)


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append({"url": url, "headers": headers, "json": json})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(responses, **overrides):
    client = PlacesClient(api_key="test-key", delay_s=0.0, max_retries=3, **overrides)
    client._session = FakeSession(responses)
    return client


def ok_payload():
    return {"places": [{"id": "p1"}], "nextPageToken": ""}


def quota_payload():
    return {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                      "message": ("Quota exceeded for quota metric 'Requests' "
                                  "and limit 'Requests per day' of quota rule.")}}


def test_transient_errors_retry_with_exponential_backoff(monkeypatch):
    sleeps = []
    monkeypatch.setattr("places.time.sleep", lambda s: sleeps.append(s))
    client = make_client(
        [FakeResponse(503), FakeResponse(503), FakeResponse(200, ok_payload())]
    )
    data = client._post({"x": 1})
    assert data["places"] == [{"id": "p1"}]
    assert client._session.posts[0]["url"].endswith("places:searchNearby")
    assert sleeps == [1.0, 2.0]


def test_network_errors_are_retried_then_stop(monkeypatch):
    sleeps = []
    monkeypatch.setattr("places.time.sleep", lambda s: sleeps.append(s))
    client = make_client([
        requests.ConnectionError("boom"),
        FakeResponse(200, ok_payload()),
    ])
    assert client._post({})["places"] == [{"id": "p1"}]
    assert sleeps == [1.0]

    # Exhausting the retry budget raises a readable PlacesError.
    client = make_client([requests.ConnectionError("boom")] * 4)
    with pytest.raises(Exception) as exc:
        client._post({})
    assert "Network error" in str(exc.value)
    assert len(client._session.posts) == 4  # initial + 3 retries, no more


def test_quota_error_after_max_retries(monkeypatch):
    sleeps = []
    monkeypatch.setattr("places.time.sleep", lambda s: sleeps.append(s))
    client = make_client([FakeResponse(429, quota_payload())] * 4)
    with pytest.raises(PlacesQuotaError) as exc:
        client._post({})
    assert "quota appears to have been exceeded" in str(exc.value)
    assert sleeps == [1.0, 2.0, 4.0]  # backoff 1s, 2s, 4s - then stop


def test_qps_rate_limit_is_distinguished(monkeypatch):
    monkeypatch.setattr("places.time.sleep", lambda s: None)
    client = make_client([
        FakeResponse(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                     "message": "Rate Limit Exceeded for ip"}}),
    ] * 4)
    with pytest.raises(PlacesQuotaError) as exc:
        client._post({})
    assert "rate limit" in str(exc.value).lower()


def test_disabled_api_maps_to_permission_error():
    client = make_client([
        FakeResponse(403, {"error": {"code": 403, "status": "PERMISSION_DENIED",
                                     "message": ("Places API (New) has not been "
                                                 "used in project 123 before or "
                                                 "it is disabled.")}}),
    ])
    with pytest.raises(PlacesAuthError) as exc:
        client._post({})
    assert "Places API (New) is enabled" in str(exc.value)


def test_invalid_key_maps_to_auth_error_and_never_leaks_key():
    client = make_client([
        FakeResponse(401, {"error": {"code": 401, "status": "UNAUTHENTICATED",
                                     "message": "API key not valid."}}),
    ])
    with pytest.raises(PlacesAuthError) as exc:
        client._post({})
    assert "API key" in str(exc.value)
    assert "test-key" not in str(exc.value)


def test_field_mask_and_auth_header_are_sent():
    client = make_client([FakeResponse(200, ok_payload())])
    client._post({"location": {"latitude": 41.3, "longitude": 19.8}})
    headers = client._session.posts[0]["headers"]
    assert headers["X-Goog-Api-Key"] == "test-key"
    assert headers["X-Goog-FieldMask"] == FIELD_MASK
    assert "*" not in FIELD_MASK  # no wildcard field masks


def test_search_is_a_single_request(monkeypatch):
    """Nearby Search (New) has no pagination: one call = one request,
    at most 20 results."""
    monkeypatch.setattr("places.time.sleep", lambda s: None)
    client = make_client([
        FakeResponse(200, {"places": [{"id": f"p{i}"} for i in range(20)]})
    ])
    places = client.search(41.3, 19.8, 900, ["cafe"])
    assert len(places) == 20
    assert len(client._session.posts) == 1


def test_request_uses_current_nearby_search_schema(monkeypatch):
    """Pin the request shape to Google's current places.searchNearby
    reference: locationRestriction.circle + maxResultCount (NOT the old
    location/radius/pageSize fields, which the API rejects with 400)."""
    monkeypatch.setattr("places.time.sleep", lambda s: None)
    client = make_client([FakeResponse(200, {"places": []})])
    client.search(41.32, 19.81, 900, ["cafe", "restaurant"])

    payload = client._session.posts[0]["json"]
    assert payload["locationRestriction"] == {
        "circle": {
            "center": {"latitude": 41.32, "longitude": 19.81},
            "radius": 900.0,
        }
    }
    assert payload["maxResultCount"] == 20
    assert payload["includedTypes"] == ["cafe", "restaurant"]
    assert payload["rankPreference"] in ("DISTANCE", "POPULARITY")
    # The fields the live API rejects with "Unknown name ... Cannot find field"
    assert "location" not in payload
    assert "radius" not in payload
    assert "pageSize" not in payload
    assert "pageToken" not in payload


def test_included_types_capped_at_google_limit(monkeypatch):
    monkeypatch.setattr("places.time.sleep", lambda s: None)
    client = make_client([FakeResponse(200, {"places": []})])
    client.search(41.32, 19.81, 900, [f"type_{i}" for i in range(60)])
    payload = client._session.posts[0]["json"]
    assert len(payload["includedTypes"]) == 50  # Google's documented limit
