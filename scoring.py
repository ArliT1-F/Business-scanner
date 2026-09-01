"""Transparent prospect/lead scoring.

The score estimates how attractive a business is as a prospect for
website-development services: businesses that look established (many
reviews, good rating, reachable by phone) but have NO website listed on
Google Maps score highest.

All the scoring policy lives in the tables below - adjust them freely.
Nothing else in the project hard-codes scoring logic.

Current maximum possible score: 40 (reviews) + 25 (rating)
                              + 5  (phone) + 30 (no website) = 100
"""
from __future__ import annotations

# (minimum_review_count, bonus) - checked top-down, first match wins.
REVIEW_BONUS = (
    (300, 40),
    (100, 30),
    (50, 20),
    (20, 10),
)

# (minimum_rating, bonus) - checked top-down, first match wins.
RATING_BONUS = (
    (4.7, 25),
    (4.5, 20),
    (4.2, 10),
)

PHONE_BONUS = 5          # a business with a phone number is contactable
NO_WEBSITE_BONUS = 30    # the core signal: no website on Google Maps


def review_bonus(review_count) -> int:
    """Bonus for the review-count band."""
    if not review_count:
        return 0
    for threshold, bonus in REVIEW_BONUS:
        if review_count >= threshold:
            return bonus
    return 0


def rating_bonus(rating) -> int:
    """Bonus for the rating band (None / unrated gets no bonus)."""
    if rating is None:
        return 0
    for threshold, bonus in RATING_BONUS:
        if rating >= threshold:
            return bonus
    return 0


def compute_prospect_score(
    review_count=None,
    rating=None,
    phone="",
    website="",
) -> int:
    """Compute the prospect score for one business.

    Args:
        review_count: number of Google reviews (int; None treated as 0).
        rating: Google rating 1.0-5.0 (None = unrated, no bonus).
        phone: phone number string ("" or None = not available).
        website: websiteUri string ("" or None = none listed on Google Maps).
    """
    score = review_bonus(review_count)
    score += rating_bonus(rating)
    if phone:
        score += PHONE_BONUS
    if not website:
        score += NO_WEBSITE_BONUS
    return score