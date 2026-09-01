"""Tests for API response normalization (places.normalize_place)."""
from __future__ import annotations

import copy

from places import normalize_place

BASE = {
    "id": "ChIJd44p3hpgwokRtOJcW4FpJ2Q",
    "displayName": {"text": "Qesaria Restaurant"},
    "formattedAddress": "Rruga e Kavajës 12, Tiranë",
    "nationalPhoneNumber": "051 123 456",
    "rating": 4.8,
    "userRatingCount": 342,
    "websiteUri": "https://qesaria.al",
    "googleMapsUri": "https://maps.google.com/?cid=123",
    "location": {"latitude": 41.327, "longitude": 19.818},
    "types": ["restaurant", "food", "point_of_interest", "establishment"],
    "primaryType": "restaurant",
}


def _place(**overrides) -> dict:
    place = copy.deepcopy(BASE)
    for key, value in overrides.items():
        if value is None:
            place.pop(key, None)
        else:
            place[key] = value
    return place


def test_complete_place() -> None:
    rec = normalize_place(copy.deepcopy(BASE))
    assert rec is not None
    assert rec["place_id"] == BASE["id"]
    assert rec["name"] == "Qesaria Restaurant"
    assert rec["address"] == BASE["formattedAddress"]
    assert rec["phone"] == "051 123 456"
    assert rec["rating"] == 4.8
    assert rec["review_count"] == 342
    assert rec["website"] == "https://qesaria.al"
    assert rec["has_google_maps_website"] == 1
    assert rec["maps_uri"] == "https://maps.google.com/?cid=123"
    assert rec["latitude"] == 41.327
    assert rec["longitude"] == 19.818
    assert rec["primary_type"] == "restaurant"
    assert rec["types"] == "restaurant,food,point_of_interest,establishment"


def test_missing_website_means_no_website_on_google_maps() -> None:
    rec = normalize_place(_place(websiteUri=None))
    assert rec is not None
    assert rec["website"] == ""
    assert rec["has_google_maps_website"] == 0


def test_missing_phone_defaults_to_empty_string() -> None:
    rec = normalize_place(_place(nationalPhoneNumber=None))
    assert rec is not None
    assert rec["phone"] == ""


def test_missing_rating_defaults_to_none_and_zero_reviews() -> None:
    rec = normalize_place(_place(rating=None, userRatingCount=None))
    assert rec is not None
    assert rec["rating"] is None
    assert rec["review_count"] == 0


def test_missing_location_defaults_to_none() -> None:
    rec = normalize_place(_place(location=None))
    assert rec is not None
    assert rec["latitude"] is None
    assert rec["longitude"] is None


def test_missing_optional_fields_do_not_crash() -> None:
    rec = normalize_place({"id": "ChIJonlyid", "displayName": {"text": "X"}})
    assert rec is not None
    assert rec["place_id"] == "ChIJonlyid"
    assert rec["name"] == "X"
    assert rec["address"] == ""
    assert rec["phone"] == ""
    assert rec["rating"] is None
    assert rec["review_count"] == 0
    assert rec["website"] == ""
    assert rec["latitude"] is None
    assert rec["longitude"] is None
    assert rec["primary_type"] == ""
    assert rec["types"] == ""


def test_place_without_id_is_skipped() -> None:
    assert normalize_place({"displayName": {"text": "No id"}}) is None
    assert normalize_place({}) is None
    assert normalize_place(None) is None


def test_display_name_without_text() -> None:
    place = copy.deepcopy(BASE)
    place["displayName"] = {}
    rec = normalize_place(place)
    assert rec is not None
    assert rec["name"] == ""
