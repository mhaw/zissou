import os
from google.cloud import firestore
from google.cloud.exceptions import GoogleCloudError
from app.models.item import Item
from datetime import datetime
import logging
from cachetools import cached, TTLCache
from app.services.firestore_helpers import (
    clear_cached_functions,
    ensure_db_client,
    normalise_timestamp,
)

logger = logging.getLogger(__name__)

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
    item_fields = set(Item.__dataclass_fields__)
    filtered_data = {k: v for k, v in item_data.items() if k in item_fields}

    return Item(**filtered_data)


@cached(cache=TTLCache(maxsize=128, ttl=600))
def get_item(item_id: str) -> Item | None:
    """Retrieves a single item by its ID."""
    _require_db()
    try:
        item_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_ITEMS")).document(
            item_id
        )
        doc = item_ref.get()
        if not doc.exists:
            return None
        return _doc_to_item(doc)
    except GoogleCloudError as e:
        logger.error(f"Firestore error retrieving item {item_id}: {e}")
        raise FirestoreError(
            f"Failed to retrieve item {item_id} from Firestore."
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error retrieving item {item_id}: {e}")
        raise FirestoreError(
            f"An unexpected error occurred while retrieving item {item_id}."
        ) from e


@cached(cache=TTLCache(maxsize=128, ttl=600))
def find_item_by_source_url(source_url: str) -> Item | None:
    """Returns the most recent item with the provided source URL, if any."""
    _require_db()
    try:
        items_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_ITEMS"))
        query = (
            items_ref.where("sourceUrl", "==", source_url)
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(1)
        )
        docs = list(query.stream())
        if not docs:
            return None
        return _doc_to_item(docs[0])
    except GoogleCloudError as e:
        logger.error(f"Firestore error finding item by source URL {source_url}: {e}")
        raise FirestoreError(f"Failed to find item by source URL {source_url}.") from e
    except Exception as e:
        logger.error(f"Unexpected error finding item by source URL {source_url}: {e}")
        raise FirestoreError(
            "An unexpected error occurred while finding item by source URL."
        ) from e


@cached(cache=TTLCache(maxsize=128, ttl=600))
def list_items(
    q: str = None,
    sort: str = "-createdAt",
    tags: tuple[str, ...] | None = None,
    bucket_id: str = None,
    duration: str = None,
    after: str = None,
    limit: int = 25,
) -> tuple[list[Item], str | None]:
    """Lists all items with filtering, sorting, and cursor-based pagination.
    Returns a tuple containing the list of items and the next_cursor (item_id) or None.
    """
    _require_db()
    try:
        items_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_ITEMS"))
        query = items_ref

        # Apply filters
        if bucket_id:
            query = query.where("buckets", "array_contains", bucket_id)
        if tags:
            if len(tags) == 1:
                query = query.where("tags", "array_contains", tags[0])
            else:
                query = query.where("tags", "array_contains_any", list(tags[:10]))

        # Duration filter
        if duration == "short":  # < 5 minutes
            query = query.where("durationSeconds", "<", 300)
        elif duration == "medium":  # 5-15 minutes
            query = query.where("durationSeconds", ">=", 300).where(
                "durationSeconds", "<=", 900
            )
        elif duration == "long":  # > 15 minutes
            query = query.where("durationSeconds", ">", 900)

        # Search query (basic title search for now due to Firestore limitations)
        # For full-text search across multiple fields (title, source host, tags),
        # a dedicated search service (e.g., Algolia, ElasticSearch) or a more complex
        # Firestore indexing strategy would be required.
        if q:
            # This performs a prefix match, not a 'contains' search.
            # For 'contains', client-side filtering or a dedicated search index is needed.
            query = query.order_by("title").start_at([q]).end_at([q + "\uf8ff"])

        # Apply sorting
        sort_direction = (
            firestore.Query.DESCENDING
            if sort.startswith("-")
            else firestore.Query.ASCENDING
        )
        sort_field = sort.lstrip("-")

        # Ensure consistent ordering for pagination, especially if sort_field is not unique
        if (
            sort_field != "createdAt"
        ):  # Always order by createdAt for consistent pagination if not primary sort
            query = query.order_by(sort_field, direction=sort_direction).order_by(
                "createdAt", direction=firestore.Query.DESCENDING
            )
        else:
            query = query.order_by(sort_field, direction=sort_direction)

        # Cursor-based pagination
        if after:
            # Fetch the document corresponding to the 'after' cursor
            start_after_doc = items_ref.document(after).get()
            if start_after_doc.exists:
                query = query.start_after(start_after_doc)
            else:
                logger.warning(
                    f"Cursor document {after} not found. Starting from beginning."
                )

        # Fetch one extra item to determine if there's a next page
        docs = query.limit(limit + 1).stream()
        items = [_doc_to_item(doc) for doc in docs]

        next_cursor = None
        if len(items) > limit:
            next_cursor = items[limit].id
            items = items[:limit]

        return items, next_cursor

    except GoogleCloudError as e:
        logger.error(f"Firestore error listing items: {e}")
        raise FirestoreError("Failed to list items from Firestore.") from e
    except Exception as e:
        logger.error(f"Unexpected error listing items: {e}")
        raise FirestoreError("An unexpected error occurred while listing items.") from e


def create_item(item: Item) -> str:
    """Creates a new item in Firestore and returns its ID."""
    _require_db()
    try:
        item_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_ITEMS")).document()

        item_data = item.__dict__
        item_data["createdAt"] = datetime.utcnow()
        item_data["updatedAt"] = datetime.utcnow()

        item_ref.set(item_data)
        clear_cached_functions(
            get_item, list_items, find_item_by_source_url, get_all_unique_tags
        )
        return item_ref.id
    except GoogleCloudError as e:
        logger.error(f"Firestore error creating item: {e}")
        raise FirestoreError("Failed to create item in Firestore.") from e
    except Exception as e:
        logger.error(f"Unexpected error creating item: {e}")
        raise FirestoreError("An unexpected error occurred while creating item.") from e


def update_item_buckets(item_id: str, bucket_ids: list[str]):
    """Updates the list of buckets for a given item."""
    _require_db()
    try:
        item_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_ITEMS")).document(
            item_id
        )

        item_ref.update({"buckets": bucket_ids, "updatedAt": datetime.utcnow()})
        clear_cached_functions(
            get_item, list_items, find_item_by_source_url, get_all_unique_tags
        )
    except GoogleCloudError as e:
        logger.error(f"Firestore error updating buckets for item {item_id}: {e}")
        raise FirestoreError(
            f"Failed to update buckets for item {item_id} in Firestore."
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error updating buckets for item {item_id}: {e}")
        raise FirestoreError(
            f"An unexpected error occurred while updating buckets for item {item_id}."
        ) from e


def update_item_tags(item_id: str, tags: list[str]):
    """Updates the list of tags for a given item."""
    _require_db()
    try:
        item_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_ITEMS")).document(
            item_id
        )

        item_ref.update({"tags": tags, "updatedAt": datetime.utcnow()})
        clear_cached_functions(
            get_item, list_items, find_item_by_source_url, get_all_unique_tags
        )
    except GoogleCloudError as e:
        logger.error(f"Firestore error updating tags for item {item_id}: {e}")
        raise FirestoreError(
            f"Failed to update tags for item {item_id} in Firestore."
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error updating tags for item {item_id}: {e}")
        raise FirestoreError(
            f"An unexpected error occurred while updating tags for item {item_id}."
        ) from e


@cached(cache=TTLCache(maxsize=1, ttl=3600))  # Cache for 1 hour
def get_all_unique_tags() -> list[str]:
    """Retrieves all unique tags from all items."""
    _require_db()
    try:
        tags = set()
        # Fetch all items (or a reasonable subset if there are too many)
        # For very large collections, this might need optimization (e.g., map-reduce on tags)
        items_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_ITEMS"))
        docs = items_ref.stream()
        for doc in docs:
            item_data = doc.to_dict()
            if "tags" in item_data and isinstance(item_data["tags"], list):
                for tag in item_data["tags"]:
                    if isinstance(tag, str):
                        tags.add(tag)
        return sorted(list(tags))
    except GoogleCloudError as e:
        logger.error(f"Firestore error retrieving unique tags: {e}")
        raise FirestoreError("Failed to retrieve unique tags from Firestore.") from e
    except Exception as e:
        logger.error(f"Unexpected error retrieving unique tags: {e}")
        raise FirestoreError(
            "An unexpected error occurred while retrieving unique tags."
        ) from e


def delete_item(item_id: str) -> bool:
    """Delete an item document from Firestore."""
    _require_db()
    try:
        item_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_ITEMS")).document(
            item_id
        )
        doc = item_ref.get()
        if not doc.exists:
            return False

        item_ref.delete()
        clear_cached_functions(
            get_item, list_items, find_item_by_source_url, get_all_unique_tags
        )
        return True
    except GoogleCloudError as e:
        logger.error(f"Firestore error deleting item {item_id}: {e}")
        raise FirestoreError(f"Failed to delete item {item_id} from Firestore.") from e
    except Exception as e:  # pragma: no cover - defensive guard
        logger.error(f"Unexpected error deleting item {item_id}: {e}")
        raise FirestoreError(
            f"An unexpected error occurred while deleting item {item_id}."
        ) from e
