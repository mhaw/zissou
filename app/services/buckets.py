import os
from datetime import datetime, timezone
import logging

from cachetools import cached, TTLCache
from google.cloud.exceptions import GoogleCloudError

from app.models.bucket import Bucket
from app.services.firestore_helpers import (
    clear_cached_functions,
    ensure_db_client,
    normalise_timestamp,
)

# Use the shared client from the items service to ensure single instantiation
from .items import db, FirestoreError

from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

BUCKETS_COLLECTION = os.getenv("FIRESTORE_COLLECTION_BUCKETS", "buckets")
_BUCKET_FIELDS = set(Bucket.__dataclass_fields__)


def _require_db() -> None:
    ensure_db_client(
        db,
        FirestoreError,
        "Firestore client is not initialized. Check application startup logs.",
    )


def _timestamp_to_sortable(value: datetime | None) -> float:
    if not value:
        return float("-inf")
    try:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    except (OSError, OverflowError, ValueError):
        return float("-inf")


def _bucket_recency_key(bucket: Bucket) -> tuple[float, str, str]:
    score = max(
        _timestamp_to_sortable(bucket.createdAt),
        _timestamp_to_sortable(bucket.updatedAt),
    )
    fallback_name = (bucket.name or "").lower()
    fallback_id = bucket.id or ""
    return (-score, fallback_name, fallback_id)


def _doc_to_bucket(doc) -> Bucket:
    """Converts a Firestore document to a Bucket dataclass."""
    data = doc.to_dict() or {}
    data["id"] = doc.id
    for field in ("createdAt", "updatedAt"):
        if field in data:
            data[field] = normalise_timestamp(data[field])
    filtered = {key: value for key, value in data.items() if key in _BUCKET_FIELDS}
    return Bucket(**filtered)


@cached(cache=TTLCache(maxsize=32, ttl=600))
def get_bucket_by_slug(slug: str) -> Bucket | None:
    """Retrieves a single bucket by its slug."""
    _require_db()
    try:
        buckets_ref = db.collection(BUCKETS_COLLECTION)
        query = buckets_ref.where("slug", "==", slug).limit(1)
        docs = list(query.stream())
        if not docs:
            return None
        return _doc_to_bucket(docs[0])
    except GoogleCloudError as e:
        logger.error(f"Firestore error retrieving bucket by slug {slug}: {e}")
        raise FirestoreError(
            f"Failed to retrieve bucket by slug {slug} from Firestore."
        ) from e


@cached(cache=TTLCache(maxsize=128, ttl=600))
def get_bucket(bucket_id: str) -> Bucket | None:
    """Retrieves a single bucket by its ID."""
    _require_db()
    try:
        bucket_ref = db.collection(BUCKETS_COLLECTION).document(bucket_id)
        doc = bucket_ref.get()
        if not doc.exists:
            return None
        return _doc_to_bucket(doc)
    except GoogleCloudError as e:
        logger.error(f"Firestore error retrieving bucket {bucket_id}: {e}")
        raise FirestoreError(
            f"Failed to retrieve bucket {bucket_id} from Firestore."
        ) from e


@cached(cache=TTLCache(maxsize=1, ttl=600))
def list_buckets() -> list[Bucket]:
    """Lists all buckets."""
    _require_db()
    try:
        buckets_ref = db.collection(BUCKETS_COLLECTION)
        docs = buckets_ref.stream()
        return [_doc_to_bucket(doc) for doc in docs]
    except GoogleCloudError as e:
        logger.error(f"Firestore error listing buckets: {e}")
        raise FirestoreError("Failed to list buckets from Firestore.") from e


@cached(cache=TTLCache(maxsize=8, ttl=300))
def list_recent_buckets(limit: int = 4) -> list[Bucket]:
    """Returns the most recently created or updated buckets."""
    buckets = list(list_buckets())
    if limit <= 0:
        return []
    buckets.sort(key=_bucket_recency_key)
    return buckets[:limit]


def create_bucket(
    name: str,
    slug: str,
    description: str,
    rss_author_name: Optional[str] = None,
    rss_owner_email: Optional[str] = None,
    rss_cover_image_url: Optional[str] = None,
    itunes_categories: Optional[list[str]] = None,
):
    """Creates a new bucket."""
    _require_db()
    try:
        bucket_ref = db.collection(BUCKETS_COLLECTION).document()
        now = datetime.utcnow()
        new_bucket = Bucket(
            name=name,
            slug=slug,
            description=description,
            rss_author_name=rss_author_name,
            rss_owner_email=rss_owner_email,
            rss_cover_image_url=rss_cover_image_url,
            itunes_categories=itunes_categories or [],
            createdAt=now,
            updatedAt=now,
        )
        payload = {
            k: v
            for k, v in new_bucket.__dict__.items()
            if k in _BUCKET_FIELDS and k != "id"
        }
        bucket_ref.set(payload)
        clear_cached_functions(
            (list_buckets, list_recent_buckets, get_bucket, get_bucket_by_slug)
        )
        return bucket_ref.id
    except GoogleCloudError as e:
        logger.error(f"Firestore error creating bucket {name} ({slug}): {e}")
        raise FirestoreError(
            f"Failed to create bucket {name} ({slug}) in Firestore."
        ) from e
