from __future__ import annotations
import os
from google.cloud import firestore  # type: ignore[attr-defined]
from google.cloud.firestore_v1 import FieldFilter  # Import FieldFilter
from google.cloud.exceptions import GoogleCloudError
from app.models.item import Item
from datetime import datetime, timezone
import logging
from cachetools import cached, TTLCache
from typing import Optional, List, Tuple
from app.services.firestore_helpers import (
    clear_cached_functions,
    ensure_db_client,
    normalise_timestamp,
)
from app.services import users as users_service
import random

logger = logging.getLogger(__name__)

FIRESTORE_COLLECTION_ITEMS = os.getenv("FIRESTORE_COLLECTION_ITEMS", "items")

# Initialize the Firestore client once at the module level.
try:
    db = firestore.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT"))
except Exception as e:
    logger.critical(f"Failed to initialize Firestore client: {e}")
    db = None


class FirestoreError(Exception):
    """Custom exception for Firestore related errors."""

    pass


def _require_db() -> None:
    ensure_db_client(
        db,
        FirestoreError,
        "Firestore client is not initialized. Check application startup logs.",
    )


def _doc_to_item(doc) -> Item:
    """Converts a Firestore document to an Item dataclass, parsing date strings."""
    item_data = doc.to_dict()
    item_data["id"] = doc.id

    for date_field in ["publishedAt", "createdAt", "updatedAt"]:
        if date_field in item_data:
            raw_value = item_data[date_field]
            normalised = normalise_timestamp(raw_value)
            if normalised is None and raw_value:
                logger.warning(
                    "Could not normalise date value '%s' for field '%s' in item %s. Leaving as None.",
                    raw_value,
                    date_field,
                    doc.id,
                )
            item_data[date_field] = normalised

    # Filter out unexpected fields to prevent errors
    # item_fields = set(Item.__dataclass_fields__)
    # filtered_data = {k: v for k, v in item_data.items() if k in item_fields}

    return Item.from_dict(item_data)


@cached(cache=TTLCache(maxsize=128, ttl=600))
def get_item(item_id: str) -> Item | None:
    """Retrieves a single item by its ID."""
    _require_db()
    try:
        item_ref = db.collection(FIRESTORE_COLLECTION_ITEMS).document(item_id)
        doc = item_ref.get()
        if not doc.exists:
            return None
        return _doc_to_item(doc)
    except GoogleCloudError as e:
        logger.error(f"Firestore error retrieving item {item_id}: {e}", exc_info=True)
        raise FirestoreError(
            f"Failed to retrieve item {item_id} from Firestore."
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error retrieving item {item_id}: {e}", exc_info=True)
        raise FirestoreError(
            f"An unexpected error occurred while retrieving item {item_id}."
        ) from e


@cached(cache=TTLCache(maxsize=128, ttl=600))
def find_item_by_source_url(source_url: str) -> Item | None:
    """Returns the most recent item with the provided source URL, if any."""
    _require_db()
    try:
        items_ref = db.collection(FIRESTORE_COLLECTION_ITEMS)
        query = (
            items_ref.where(filter=FieldFilter("sourceUrl", "==", source_url))
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(1)
        )
        docs = list(query.stream())
        if not docs:
            return None
        return _doc_to_item(docs[0])
    except GoogleCloudError as e:
        logger.error(
            f"Firestore error finding item by source URL {source_url}: {e}",
            exc_info=True,
        )
        raise FirestoreError(f"Failed to find item by source URL {source_url}.") from e
    except Exception as e:
        logger.error(
            f"Unexpected error finding item by source URL {source_url}: {e}",
            exc_info=True,
        )
        raise FirestoreError(
            "An unexpected error occurred while finding item by source URL."
        ) from e


def get_random_unread_item(user_id: str) -> Item | None:
    """Retrieves a random unread item for the given user."""
    ensure_db_client(
        db,
        FirestoreError,
        "Firestore client is not initialized. Check application startup logs.",
    )
    try:
        items_ref = db.collection(FIRESTORE_COLLECTION_ITEMS)
        query = (
            items_ref.where(filter=FieldFilter("userId", "==", user_id))
            .where(filter=FieldFilter("is_read", "==", False))
            .where(filter=FieldFilter("is_archived", "==", False))
        )

        # Firestore doesn't support random ordering directly.
        # Fetch a small, random sample and pick one.
        # This approach has limitations for very large collections.
        docs = list(query.limit(100).stream())
        if not docs:
            return None

        random_doc = random.choice(docs)
        return Item.from_dict(random_doc.to_dict())
    except GoogleCloudError as e:
        logger.error(
            f"Firestore error getting random unread item for user {user_id}: {e}",
            exc_info=True,
        )
        raise FirestoreError(
            f"Failed to get random unread item for user {user_id}."
        ) from e


def toggle_read_status(item_id: str, user_id: str) -> Item:
    """Toggles the read status of an item."""
    _require_db()
    item_ref = db.collection(FIRESTORE_COLLECTION_ITEMS).document(item_id)
    item_doc = item_ref.get()

    if not item_doc.exists:
        raise ValueError(f"Item with ID {item_id} not found.")

    item_data = item_doc.to_dict()
    if item_data.get("userId") != user_id:
        raise PermissionError("User does not have permission to modify this item.")

    current_read_status = item_data.get("is_read", False)
    new_read_status = not current_read_status
    item_ref.update({"is_read": new_read_status})

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

    # Retrieve the updated item
    updated_item_doc = item_ref.get()
    return Item.from_dict(updated_item_doc.to_dict())


def list_items(
    user_id: Optional[str],
    bucket_slug: Optional[str] = None,
    search_query: Optional[str] = None,
    tags: Optional[List[str]] = None,
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
    try:
        items_ref = db.collection(FIRESTORE_COLLECTION_ITEMS)
        query = items_ref

        if user_id:
            query = query.where(filter=FieldFilter("userId", "==", user_id))
        else:
            query = query.where(filter=FieldFilter("is_public", "==", True))

        if bucket_slug:
            query = query.where(
                filter=FieldFilter("buckets", "array_contains", bucket_slug)
            )

        if search_query:
            # This is a simplified search. Full-text search would require a dedicated service.
            query = (
                query.order_by("title")
                .start_at(search_query)
                .end_at(search_query + "\uf8ff")
            )

        if tags:
            query = query.where(filter=FieldFilter("tags", "array_contains_any", tags))

        if not include_archived:
            query = query.where(filter=FieldFilter("is_archived", "==", False))

        if not include_read:
            query = query.where(filter=FieldFilter("is_read", "==", False))

        # Apply sorting
        if sort_by == "newest":
            query = query.order_by("createdAt", direction=firestore.Query.DESCENDING)
        elif sort_by == "oldest":
            query = query.order_by("createdAt", direction=firestore.Query.ASCENDING)
        elif sort_by == "title":
            query = query.order_by("title", direction=firestore.Query.ASCENDING)
        elif sort_by == "-title":
            query = query.order_by("title", direction=firestore.Query.DESCENDING)
        elif sort_by == "durationSeconds":
            query = query.order_by(
                "durationSeconds", direction=firestore.Query.ASCENDING
            )
        elif sort_by == "-durationSeconds":
            query = query.order_by(
                "durationSeconds", direction=firestore.Query.DESCENDING
            )

        # Apply pagination
        if cursor:
            start_after_doc = items_ref.document(cursor).get()
            if start_after_doc.exists:
                query = query.start_after(start_after_doc)

        docs = query.limit(limit + 1).stream()
        items = [_doc_to_item(doc) for doc in docs]

        next_cursor = None
        if len(items) > limit:
            next_cursor = items[limit].id
            items = items[:limit]

        return items, next_cursor

    except GoogleCloudError as e:
        logger.error(
            f"Firestore error listing items for user {user_id}: {e}", exc_info=True
        )
        raise FirestoreError(
            f"Failed to list items for user {user_id} from Firestore."
        ) from e
    except Exception as e:
        logger.error(
            f"Unexpected error listing items for user {user_id}: {e}", exc_info=True
        )
        raise FirestoreError(
            f"An unexpected error occurred while listing items for user {user_id}."
        ) from e


def create_item(item: Item, user_id: str) -> str:
    """Creates a new item in Firestore and returns its ID."""
    _require_db()
    try:
        item_ref = db.collection(FIRESTORE_COLLECTION_ITEMS).document()

        item.userId = user_id
        item_data = item.__dict__
        item_data["createdAt"] = datetime.now(timezone.utc)
        item_data["updatedAt"] = datetime.now(timezone.utc)
        if item.reading_time is not None:
            item_data["reading_time"] = item.reading_time

        clear_cached_functions(
            get_item, list_items, find_item_by_source_url, get_all_unique_tags  # type: ignore[arg-type]
        )
        return item_ref.id
    except GoogleCloudError as e:
        logger.error(f"Firestore error creating item: {e}", exc_info=True)
        raise FirestoreError("Failed to create item in Firestore.") from e
    except Exception as e:
        logger.error(f"Unexpected error creating item: {e}", exc_info=True)
        raise FirestoreError("An unexpected error occurred while creating item.") from e


def update_item_buckets(item_id: str, bucket_ids: list[str]):
    """Updates the list of buckets for a given item."""
    _require_db()
    try:
        item_ref = db.collection(FIRESTORE_COLLECTION_ITEMS).document(item_id)

        item_ref.update(
            {"buckets": bucket_ids, "updatedAt": datetime.now(timezone.utc)}
        )
        clear_cached_functions(
            get_item, list_items, find_item_by_source_url, get_all_unique_tags
        )
    except GoogleCloudError as e:
        logger.error(
            f"Firestore error updating buckets for item {item_id}: {e}", exc_info=True
        )
        raise FirestoreError(
            f"Failed to update buckets for item {item_id} in Firestore."
        ) from e
    except Exception as e:
        logger.error(
            f"Unexpected error updating buckets for item {item_id}: {e}", exc_info=True
        )
        raise FirestoreError(
            f"An unexpected error occurred while updating buckets for item {item_id}."
        ) from e


def update_item_tags(item_id: str, tags: list[str]):
    """Updates the list of tags for a given item."""
    _require_db()
    try:
        item_ref = db.collection(FIRESTORE_COLLECTION_ITEMS).document(item_id)

        item_ref.update({"tags": tags, "updatedAt": datetime.now(timezone.utc)})
        clear_cached_functions(
            get_item, list_items, find_item_by_source_url, get_all_unique_tags
        )
    except GoogleCloudError as e:
        logger.error(
            f"Firestore error updating tags for item {item_id}: {e}", exc_info=True
        )
        raise FirestoreError(
            f"Failed to update tags for item {item_id} in Firestore."
        ) from e
    except Exception as e:
        logger.error(
            f"Unexpected error updating tags for item {item_id}: {e}", exc_info=True
        )
        raise FirestoreError(
            f"An unexpected error occurred while updating tags for item {item_id}."
        ) from e


def update_item_archived_status(item_id: str, is_archived: bool):
    """Updates the archived status of a given item."""
    _require_db()
    try:
        item_ref = db.collection(FIRESTORE_COLLECTION_ITEMS).document(item_id)

        item_ref.update(
            {"is_archived": is_archived, "updatedAt": datetime.now(timezone.utc)}
        )
        clear_cached_functions(
            get_item, list_items, find_item_by_source_url, get_all_unique_tags
        )
    except GoogleCloudError as e:
        logger.error(
            f"Firestore error updating archived status for item {item_id}: {e}",
            exc_info=True,
        )
        raise FirestoreError(
            f"Failed to update archived status for item {item_id} in Firestore."
        ) from e
    except Exception as e:
        logger.error(
            f"Unexpected error updating archived status for item {item_id}: {e}",
            exc_info=True,
        )
        raise FirestoreError(
            f"An unexpected error occurred while updating archived status for item {item_id}."
        ) from e


@cached(cache=TTLCache(maxsize=1, ttl=3600))  # Cache for 1 hour
def get_all_unique_tags() -> list[str]:
    """Retrieves all unique tags from all items."""
    _require_db()
    try:
        tags = set()
        # Fetch all items (or a reasonable subset if there are too many)
        # For very large collections, this might need optimization (e.g., map-reduce on tags)
        items_ref = db.collection(FIRESTORE_COLLECTION_ITEMS)
        docs = items_ref.stream()
        for doc in docs:
            item_data = doc.to_dict()
            if "tags" in item_data and isinstance(item_data["tags"], list):
                for tag in item_data["tags"]:
                    if isinstance(tag, str):
                        tags.add(tag)
        return sorted(list(tags))
    except GoogleCloudError as e:
        logger.error(f"Firestore error retrieving unique tags: {e}", exc_info=True)
        raise FirestoreError("Failed to retrieve unique tags from Firestore.") from e
    except Exception as e:
        logger.error(f"Unexpected error retrieving unique tags: {e}", exc_info=True)
        raise FirestoreError(
            "An unexpected error occurred while retrieving unique tags."
        ) from e


def delete_item(item_id: str) -> bool:
    """Delete an item document from Firestore."""
    _require_db()
    try:
        item_ref = db.collection(FIRESTORE_COLLECTION_ITEMS).document(item_id)
        doc = item_ref.get()
        if not doc.exists:
            return False

        item_ref.delete()
        clear_cached_functions(
            get_item, list_items, find_item_by_source_url, get_all_unique_tags
        )
        return True
    except GoogleCloudError as e:
        logger.error(f"Firestore error deleting item {item_id}: {e}", exc_info=True)
        raise FirestoreError(f"Failed to delete item {item_id} from Firestore.") from e
    except Exception as e:  # pragma: no cover - defensive guard
        logger.error(f"Unexpected error deleting item {item_id}: {e}", exc_info=True)
        raise FirestoreError(
            f"An unexpected error occurred while deleting item {item_id}."
        ) from e


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
    except (GoogleCloudError, AttributeError):
        logger.debug("Count aggregation not available, falling back to streaming.")
    return sum(1 for _ in query.stream())


def get_item_count() -> int:
    """Returns the total number of items."""
    _require_db()
    try:
        items_ref = db.collection(FIRESTORE_COLLECTION_ITEMS)
        return _run_count(items_ref)
    except GoogleCloudError as e:
        logger.error(f"Firestore error getting item count: {e}", exc_info=True)
        return 0
