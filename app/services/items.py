from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from cachetools import TTLCache, cached
from google.api_core.exceptions import GoogleAPICallError
from google.cloud import firestore
from google.cloud.firestore_v1.field_path import FieldPath
from google.cloud.firestore_v1 import FieldFilter, DocumentSnapshot

from app.models.item import Item
from app.services import users as users_service
from app.services import buckets as buckets_service
from app.services.firestore_client import db, FirestoreError
from app.services.firestore_helpers import (
    clear_cached_functions,
    ensure_db_client,
)
from app.utils.firestore_errors import handle_firestore_errors
from app.config import AppConfig
from app.signals import item_updated
from app.services.item_utils import (
    apply_filters,
    apply_sorting,
    apply_pagination,
    duration_matches,
)

logger = logging.getLogger(__name__)

_OVERSCAN_MULTIPLIER = max(1, int(os.getenv("ITEM_QUERY_OVERSCAN_MULTIPLIER", "3")))
_OVERSCAN_MAX = max(50, int(os.getenv("ITEM_QUERY_OVERSCAN_MAX", "200")))


def _bucket_is_public(bucket) -> bool:
    return bool(getattr(bucket, "is_public", False) or getattr(bucket, "public", False))


def _normalise_buckets(
    bucket_ids: list[str] | None,
) -> tuple[list[str], list[str], bool]:
    """Return canonical bucket ids, slugs, and public status for the provided identifiers."""
    if not bucket_ids:
        return [], [], False

    resolved_ids: list[str] = []
    resolved_slugs: list[str] = []
    any_public = False

    for identifier in bucket_ids:
        if not identifier:
            continue
        candidate = identifier.strip()
        if not candidate:
            continue
        bucket = buckets_service.get_bucket(candidate)
        if not bucket:
            bucket = buckets_service.get_bucket_by_slug(candidate)
        if not bucket and candidate.lower() != candidate:
            bucket = buckets_service.get_bucket_by_slug(candidate.lower())
        if not bucket:
            logger.warning(
                "items.bucket_lookup_failed",
                bucket_reference=candidate,
            )
            continue

        if bucket.id and bucket.id not in resolved_ids:
            resolved_ids.append(bucket.id)

        slug = bucket.slug or bucket.id
        if slug and slug not in resolved_slugs:
            resolved_slugs.append(slug)

        if not any_public and _bucket_is_public(bucket):
            any_public = True

    return resolved_ids, resolved_slugs, any_public


def _bucket_slugs_from_ids(bucket_ids: list[str] | None) -> list[str]:
    return _normalise_buckets(bucket_ids)[1]


def _require_db() -> None:
    ensure_db_client(
        db,
        FirestoreError,
        "Firestore client is not initialized. Check application startup logs.",
    )


def _doc_to_item(doc: DocumentSnapshot) -> Item:
    """Converts a Firestore document to an Item dataclass."""
    item_data = doc.to_dict()
    item_data["id"] = doc.id
    return Item.from_dict(item_data)


@handle_firestore_errors
@cached(cache=TTLCache(maxsize=128, ttl=600))
def get_item(item_id: str) -> Item | None:
    """Retrieves a single item by its ID."""
    _require_db()
    item_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS).document(item_id)
    doc = item_ref.get()
    if not doc.exists:
        return None
    return _doc_to_item(doc)


@handle_firestore_errors
@cached(cache=TTLCache(maxsize=1024, ttl=3600))
def get_items_by_ids(item_ids: List[str]) -> List[Item]:
    """Retrieves multiple items by their IDs."""
    _require_db()
    refs = [
        db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS).document(id)
        for id in item_ids
    ]
    docs = db.get_all(refs)
    return [_doc_to_item(doc) for doc in docs if doc.exists]


@handle_firestore_errors
@cached(cache=TTLCache(maxsize=128, ttl=600))
def find_item_by_source_url(source_url: str) -> Item | None:
    """Returns the most recent item with the provided source URL, if any."""
    _require_db()
    items_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS)
    query = (
        items_ref.where(filter=FieldFilter("sourceUrl", "==", source_url))
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(1)
    )
    docs = list(query.stream())
    if not docs:
        return None
    return _doc_to_item(docs[0])


@handle_firestore_errors
def get_random_unread_item(user_id: str) -> Item | None:
    """Retrieves a random unread item for the given user."""
    _require_db()
    items_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS)
    query = (
        items_ref.where(filter=FieldFilter("userId", "==", user_id))
        .where(filter=FieldFilter("is_read", "==", False))
        .where(filter=FieldFilter("is_archived", "==", False))
    )

    # This is not truly random, but it's a more efficient way to get a random-ish item
    # without reading all documents.
    random_key = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS).document().id
    query = (
        query.order_by(FieldPath.document_id())
        .start_at(random_key)
        .limit(1)
    )
    docs = list(query.stream())
    if not docs:
        # If we didn't find an item, try starting from the beginning of the collection
        query = query.order_by(FieldPath.document_id()).limit(1)
        docs = list(query.stream())
        if not docs:
            return None

    return _doc_to_item(docs[0])


@firestore.transactional
def toggle_read_status_transaction(transaction, item_ref, user_id):
    item_doc = item_ref.get(transaction=transaction)
    if not item_doc.exists:
        raise ValueError(f"Item with ID {item_ref.id} not found.")

    item_data = item_doc.to_dict()
    if item_data.get("userId") != user_id:
        raise PermissionError("User does not have permission to modify this item.")

    current_read_status = item_data.get("is_read", False)
    new_read_status = not current_read_status
    transaction.update(
        item_ref, {"is_read": new_read_status, "updatedAt": datetime.now(timezone.utc)}
    )

    # Send a signal to invalidate the feed cache
    associated_buckets = item_data.get("buckets", [])
    item_updated.send(
        "items",
        bucket_ids=associated_buckets,
        bucket_slugs=_bucket_slugs_from_ids(associated_buckets),
        reason="read_status_changed",
        item_id=item_ref.id,
    )

    # Update user statistics
    user = users_service.get_user(user_id)
    if user:
        update_data = {}
        if new_read_status:
            update_data["articles_listened_to"] = user.articles_listened_to + 1
            if item_data.get("reading_time"):
                update_data["total_listening_time"] = (
                    user.total_listening_time + item_data["reading_time"]
                )
        else:
            # If marking unread, decrement stats (ensure not to go below zero)
            update_data["articles_listened_to"] = max(0, user.articles_listened_to - 1)
            if item_data.get("reading_time"):
                update_data["total_listening_time"] = max(
                    0, user.total_listening_time - item_data["reading_time"]
                )

        if update_data:
            users_service.update_user(user_id, update_data)

    updated_item_doc = item_ref.get(transaction=transaction)
    return _doc_to_item(updated_item_doc)


def toggle_read_status(item_id: str, user_id: str) -> Item:
    """Toggles the read status of an item."""
    _require_db()
    item_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS).document(item_id)
    transaction = db.transaction()
    return toggle_read_status_transaction(transaction, item_ref, user_id)


@handle_firestore_errors
def list_items(
    user_id: Optional[str],
    bucket_slug: Optional[str] = None,
    search_query: Optional[str] = None,
    tags: Optional[List[str]] = None,
    duration: Optional[str] = None,
    sort_by: str = "newest",
    cursor: Optional[str] = None,
    limit: int = 20,
    include_archived: bool = False,
    include_read: bool = False,
) -> Tuple[List[Item], str | None]:
    """
    Lists items for a user with various filtering, sorting, and pagination options.
    """
    _require_db()
    items_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS)
    query = items_ref

    query = apply_filters(
        query, user_id, bucket_slug, tags, include_archived, include_read
    )
    query = apply_sorting(query, sort_by, search_query)
    query = apply_pagination(query, cursor, items_ref)

    overscan_limit = min(max(limit, 1) * _OVERSCAN_MULTIPLIER, _OVERSCAN_MAX)
    docs = list(query.limit(overscan_limit + 1).stream())

    items: list[Item] = []
    next_cursor: str | None = None

    for idx, doc in enumerate(docs):
        item = _doc_to_item(doc)
        if not duration_matches(item, duration):
            continue

        if len(items) < limit:
            items.append(item)
            if len(items) == limit:
                if idx < len(docs) - 1 or len(docs) == overscan_limit + 1:
                    next_cursor = doc.id
                    break
        else:
            next_cursor = doc.id
            break
    else:
        if len(docs) == overscan_limit + 1:
            next_cursor = docs[-1].id

    return items, next_cursor


@handle_firestore_errors
def create_item(item: Item, user_id: str) -> str:
    """Creates a new item in Firestore and returns its ID."""
    _require_db()
    item_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS).document()

    item.userId = user_id
    resolved_bucket_ids, _, any_public = _normalise_buckets(item.buckets)
    if resolved_bucket_ids:
        item.buckets = resolved_bucket_ids
    if any_public:
        item.is_public = True

    item_data = item.__dict__
    item_data["createdAt"] = datetime.now(timezone.utc)
    item_data["updatedAt"] = datetime.now(timezone.utc)
    if item.reading_time is not None:
        item_data["reading_time"] = item.reading_time

    item_ref.set(item_data)

    clear_cached_functions(
        get_item, list_items, find_item_by_source_url, get_all_unique_tags  # type: ignore[arg-type]
    )
    return item_ref.id


@handle_firestore_errors
def update_item_buckets(item_id: str, bucket_ids: list[str]):
    """Updates the list of buckets for a given item."""
    _require_db()
    item_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS).document(item_id)
    # Get current item data to determine old buckets for cache invalidation
    current_item_doc = item_ref.get()
    if not current_item_doc.exists:
        raise ValueError(f"Item with ID {item_id} not found.")
    current_item_data = current_item_doc.to_dict()
    old_buckets = current_item_data.get("buckets", [])

    resolved_ids, slugs, any_public = _normalise_buckets(bucket_ids)
    now = datetime.now(timezone.utc)
    update_data: dict[str, object] = {
        "buckets": resolved_ids,
        "updatedAt": now,
    }
    update_data["is_public"] = any_public

    item_ref.update(update_data)
    clear_cached_functions(
        get_item, list_items, find_item_by_source_url, get_all_unique_tags
    )

    # Send a signal to invalidate the feed cache
    item_updated.send(
        "items",
        bucket_ids=old_buckets,
        bucket_slugs=_bucket_slugs_from_ids(old_buckets),
        reason="buckets_removed",
        item_id=item_id,
    )
    item_updated.send(
        "items",
        bucket_ids=resolved_ids,
        bucket_slugs=slugs,
        reason="buckets_updated",
        item_id=item_id,
    )


@handle_firestore_errors
def update_item_tags(item_id: str, tags: list[str]):
    """Updates the list of tags for a given item."""
    _require_db()
    item_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS).document(item_id)
    # Get current item data to determine associated buckets for cache invalidation
    current_item_doc = item_ref.get()
    if not current_item_doc.exists:
        raise ValueError(f"Item with ID {item_id} not found.")
    current_item_data = current_item_doc.to_dict()
    associated_buckets = current_item_data.get("buckets", [])

    # Send a signal to invalidate the feed cache
    item_updated.send(
        "items",
        bucket_ids=associated_buckets,
        bucket_slugs=_bucket_slugs_from_ids(associated_buckets),
        reason="tags_updated",
        item_id=item_id,
    )


@handle_firestore_errors
def update_item_archived_status(item_id: str, is_archived: bool):
    """Updates the archived status of a given item."""
    _require_db()
    item_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS).document(item_id)
    # Get current item data to determine associated buckets for cache invalidation
    current_item_doc = item_ref.get()
    if not current_item_doc.exists:
        raise ValueError(f"Item with ID {item_id} not found.")
    current_item_data = current_item_doc.to_dict()
    associated_buckets = current_item_data.get("buckets", [])

    item_ref.update(
        {"is_archived": is_archived, "updatedAt": datetime.now(timezone.utc)}
    )
    clear_cached_functions(
        get_item, list_items, find_item_by_source_url, get_all_unique_tags
    )

    # Send a signal to invalidate the feed cache
    item_updated.send(
        "items",
        bucket_ids=associated_buckets,
        bucket_slugs=_bucket_slugs_from_ids(associated_buckets),
        reason="archive_status_changed",
        item_id=item_id,
    )


@handle_firestore_errors
def update_item_summary(item_id: str, summary: str | None):
    """Persist an AI-generated summary for an item."""
    _require_db()
    item_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS).document(item_id)
    # Get current item data to determine associated buckets for cache invalidation
    current_item_doc = item_ref.get()
    if not current_item_doc.exists:
        raise ValueError(f"Item with ID {item_id} not found.")
    current_item_data = current_item_doc.to_dict()
    associated_buckets = current_item_data.get("buckets", [])

    item_ref.update({"summary_text": summary, "updatedAt": datetime.now(timezone.utc)})
    clear_cached_functions(get_item)

    # Send a signal to invalidate the feed cache
    item_updated.send(
        "items",
        bucket_ids=associated_buckets,
        bucket_slugs=_bucket_slugs_from_ids(associated_buckets),
        reason="summary_updated",
        item_id=item_id,
    )


@handle_firestore_errors
def update_item_auto_tags(item_id: str, tags: list[str]):
    """Persist automatically generated tags without disturbing manual tags."""
    _require_db()
    cleaned = [tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()]
    item_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS).document(item_id)
    # Get current item data to determine associated buckets for cache invalidation
    current_item_doc = item_ref.get()
    if not current_item_doc.exists:
        raise ValueError(f"Item with ID {item_id} not found.")
    current_item_data = current_item_doc.to_dict()
    associated_buckets = current_item_data.get("buckets", [])

    item_ref.update({"auto_tags": cleaned, "updatedAt": datetime.now(timezone.utc)})
    clear_cached_functions(get_item)

    # Send a signal to invalidate the feed cache
    item_updated.send(
        "items",
        bucket_ids=associated_buckets,
        bucket_slugs=_bucket_slugs_from_ids(associated_buckets),
        reason="auto_tags_updated",
        item_id=item_id,
    )


@handle_firestore_errors
@cached(cache=TTLCache(maxsize=1, ttl=3600))  # Cache for 1 hour
def get_all_unique_tags() -> list[str]:
    """Retrieves all unique tags from all items."""
    _require_db()
    tags = set()
    items_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS)
    docs = items_ref.stream()
    for doc in docs:
        item_data = doc.to_dict()
        if "tags" in item_data and isinstance(item_data["tags"], list):
            for tag in item_data["tags"]:
                if isinstance(tag, str):
                    tags.add(tag)
    return sorted(list(tags))


@handle_firestore_errors
def delete_item(item_id: str) -> bool:
    """Delete an item document from Firestore."""
    _require_db()
    item_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS).document(item_id)
    # Get current item data to determine associated buckets for cache invalidation
    current_item_doc = item_ref.get()
    if not current_item_doc.exists:
        return False
    current_item_data = current_item_doc.to_dict()
    associated_buckets = current_item_data.get("buckets", [])

    item_ref.delete()
    clear_cached_functions(
        get_item, list_items, find_item_by_source_url, get_all_unique_tags
    )

    # Send a signal to invalidate the feed cache
    item_updated.send(
        "items",
        bucket_ids=associated_buckets,
        bucket_slugs=_bucket_slugs_from_ids(associated_buckets),
        reason="item_deleted",
        item_id=item_id,
    )
    return True


def _run_count(query) -> int:
    """Helper to run a count aggregation query against Firestore."""
    try:
        count_query = query.count()
        count_results = list(count_query.get())
        if count_results:
            try:
                return count_results[0][0].value
            except (IndexError, TypeError, AttributeError):
                aggregation_result = count_results[0]
                if hasattr(aggregation_result, "value"):
                    return aggregation_result.value
    except (GoogleAPICallError, AttributeError):
        logger.debug("Count aggregation not available, falling back to streaming.")
    return sum(1 for _ in query.stream())


@handle_firestore_errors
def get_item_count() -> int:
    """Returns the total number of items."""
    _require_db()
    items_ref = db.collection(AppConfig.FIRESTORE_COLLECTION_ITEMS)
    return _run_count(items_ref)
