from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from google.api_core.exceptions import FailedPrecondition


def ensure_db_client(
    db, error_cls: type[Exception], message: str | None = None
) -> None:
    """Raise ``error_cls`` if the shared Firestore client is missing."""
    if db is None:
        raise error_cls(
            message
            or "Firestore client is not initialized. Check application startup logs."
        )


def clear_cached_functions(*functions: Iterable[Callable]) -> None:
    """Clears cachetools caches for the provided callables, if present."""
    for fn in functions:
        cache_obj = getattr(fn, "cache", None)
        if cache_obj and hasattr(cache_obj, "clear"):
            cache_obj.clear()


def extract_index_url(error: FailedPrecondition) -> str | None:
    """Extract the Firestore index creation URL from a `FailedPrecondition` error message."""
    match = re.search(
        r"(https://console.firebase.google.com/project/[^/]+/database/firestore/indexes\?create_composite=.*)",
        str(error),
    )
    if match:
        return match.group(1)
    return None


def normalise_timestamp(value: Any) -> datetime | None:
    """Normalizes Firestore timestamps or ISO strings into timezone-aware ``datetime`` objects."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if hasattr(value, "to_datetime"):
        try:
            converted = value.to_datetime()
        except TypeError:
            converted = value.to_datetime()
        if converted.tzinfo is None:
            converted = converted.replace(tzinfo=timezone.utc)
        return converted
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            converted = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        if converted.tzinfo is None:
            converted = converted.replace(tzinfo=timezone.utc)
        return converted
    return None
