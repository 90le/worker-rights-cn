"""Deterministic effective, expiry, and review-age checks for source cards."""

from __future__ import annotations

import re
from datetime import date
from typing import Any


DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}$")
DEFAULT_MAX_REVIEW_AGE_DAYS = 366


def parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def classify_source_health(
    source: dict[str, Any],
    as_of: date,
    max_review_age_days: int = DEFAULT_MAX_REVIEW_AGE_DAYS,
) -> dict[str, Any]:
    """Classify one source card without treating a missing expiry as perpetual."""
    reviewed = parse_iso_date(source.get("current_as_of"))
    retrieved = parse_iso_date(source.get("retrieved_at"))
    effective = parse_iso_date(source.get("effective_date")) if source.get("effective_date") is not None else None
    expiry = parse_iso_date(source.get("expiry_date")) if source.get("expiry_date") is not None else None
    invalid = (
        reviewed is None
        or retrieved is None
        or max_review_age_days <= 0
        or reviewed > as_of
        or retrieved > as_of
        or reviewed < retrieved
        or (source.get("effective_date") is not None and effective is None)
        or (source.get("expiry_date") is not None and expiry is None)
        or (effective is not None and expiry is not None and expiry < effective)
    )
    age = (as_of - reviewed).days if reviewed is not None else None
    if invalid:
        status = "invalid"
    elif effective is not None and as_of < effective:
        status = "not_yet_effective"
    elif expiry is not None and as_of > expiry:
        status = "expired"
    elif age is not None and age > max_review_age_days:
        status = "review_due"
    else:
        status = "current"
    return {
        "status": status,
        "as_of": as_of.isoformat(),
        "review_age_days": age,
        "max_review_age_days": max_review_age_days,
        "effective_date": source.get("effective_date"),
        "expiry_date": source.get("expiry_date"),
        "retrieved_at": source.get("retrieved_at"),
        "current_as_of": source.get("current_as_of"),
    }
