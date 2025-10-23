"""
This module provides utility functions for the items service.
"""

from typing import List, Optional

from google.cloud import firestore  # type: ignore[attr-defined]
from google.cloud.firestore_v1 import FieldFilter

from app.models.item import Item

_DURATION_PRESETS: dict[str, tuple[float | None, float | None]] = {
    "short": (None, 300.0),
    "medium": (300.0, 900.0),
    "long": (900.0, None),
}


def apply_filters(
    query,
    user_id: Optional[str],
    bucket_slug: Optional[str],
    tags: Optional[List[str]],
    include_archived: bool,
    include_read: bool,
):
    """Applies filters to the Firestore query."""
    if user_id:
        query = query.where(filter=FieldFilter("userId", "==", user_id))
    else:
        query = query.where(filter=FieldFilter("is_public", "==", True))

    if bucket_slug:
        query = query.where(
            filter=FieldFilter("buckets", "array_contains", bucket_slug)
        )

    if tags:
        query = query.where(filter=FieldFilter("tags", "array_contains_any", tags))

    if not include_archived:
        query = query.where(filter=FieldFilter("is_archived", "==", False))

    if not include_read:
        query = query.where(filter=FieldFilter("is_read", "==", False))

    return query


def apply_sorting(query, sort_by: str, search_query: Optional[str]):
    """Applies sorting to the Firestore query."""
    order_applied = False
    if search_query:
        query = (
            query.order_by("title")
            .start_at(search_query)
            .end_at(search_query + "\uf8ff")
        )
        order_applied = True

    if sort_by == "newest":
        query = query.order_by("createdAt", direction=firestore.Query.DESCENDING)
    elif sort_by == "oldest":
        query = query.order_by("createdAt", direction=firestore.Query.ASCENDING)
    elif sort_by == "title" and not order_applied:
        query = query.order_by("title", direction=firestore.Query.ASCENDING)
    elif sort_by == "-title":
        query = query.order_by("title", direction=firestore.Query.DESCENDING)
    elif sort_by == "durationSeconds":
        query = query.order_by("durationSeconds", direction=firestore.Query.ASCENDING)
    elif sort_by == "-durationSeconds":
        query = query.order_by("durationSeconds", direction=firestore.Query.DESCENDING)

    return query


def apply_pagination(query, cursor: Optional[str], items_ref):
    """Applies pagination to the Firestore query."""
    if cursor:
        start_after_doc = items_ref.document(cursor).get()
        if start_after_doc.exists:
            query = query.start_after(start_after_doc)
    return query


def duration_matches(item: Item, duration_key: str | None) -> bool:
    """Return True when the item satisfies the duration preset."""
    if not duration_key:
        return True
    duration = getattr(item, "durationSeconds", None)
    if duration is None:
        return False

    bounds = _DURATION_PRESETS.get(duration_key)
    if not bounds:
        return True
    lower, upper = bounds
    if lower is not None and duration < lower:
        return False
    if upper is not None and duration > upper:
        return False
    return True
