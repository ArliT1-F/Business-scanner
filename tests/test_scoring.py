"""Tests for the prospect scoring algorithm (scoring.compute_prospect_score)."""
from __future__ import annotations

import pytest

from scoring import compute_prospect_score


def test_established_without_website_beats_small_with_website() -> None:
    """The core requirement: high reviews + high rating + no website
    must score higher than few reviews + low rating (+ has website)."""
    hot = compute_prospect_score(
        review_count=412, rating=4.9, phone="+355 69 123 4567", website=""
    )
    cold = compute_prospect_score(
        review_count=5, rating=3.9, phone="", website="https://example.al"
    )
    assert hot > cold


def test_review_bands() -> None:
    # website set so the "no website" bonus stays constant.
    web = "https://x.al"
    assert compute_prospect_score(300, None, "", web) == 40
    assert compute_prospect_score(299, None, "", web) == 30
    assert compute_prospect_score(100, None, "", web) == 30
    assert compute_prospect_score(99, None, "", web) == 20
    assert compute_prospect_score(50, None, "", web) == 20
    assert compute_prospect_score(49, None, "", web) == 10
    assert compute_prospect_score(20, None, "", web) == 10
    assert compute_prospect_score(19, None, "", web) == 0
    assert compute_prospect_score(None, None, "", web) == 0


def test_rating_bands() -> None:
    web = "https://x.al"
    assert compute_prospect_score(0, 4.7, "", web) == 25
    assert compute_prospect_score(0, 4.9, "", web) == 25
    assert compute_prospect_score(0, 4.5, "", web) == 20
    assert compute_prospect_score(0, 4.2, "", web) == 10
    assert compute_prospect_score(0, 4.1, "", web) == 0
    assert compute_prospect_score(0, None, "", web) == 0  # unrated: no bonus


def test_phone_bonus() -> None:
    web = "https://x.al"
    assert compute_prospect_score(0, None, "051 123 456", web) == 5
    assert compute_prospect_score(0, None, "", web) == 0
    assert compute_prospect_score(0, None, None, web) == 0


def test_no_website_bonus() -> None:
    base = compute_prospect_score(0, None, "", "https://x.al")
    no_website = compute_prospect_score(0, None, "", "")
    assert no_website - base == 30


def test_maximum_score_is_100() -> None:
    assert compute_prospect_score(300, 4.7, "051 123 456", "") == 100
    assert compute_prospect_score(10000, 5.0, "051", None) == 100


@pytest.mark.parametrize(
    "reviews, rating, phone, website, expected",
    [
        (500, 4.8, "051", "", 100),   # 40 + 25 + 5 + 30
        (100, 4.5, "051", "", 85),    # 30 + 20 + 5 + 30
        (50, 4.2, "", "", 60),        # 20 + 10 + 0 + 30
        (10, None, "", "https://x.al", 0),  # website listed: no bonus at all
    ],
)
def test_score_examples(reviews, rating, phone, website, expected) -> None:
    assert compute_prospect_score(reviews, rating, phone, website) == expected
