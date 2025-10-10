import os
from google.cloud import firestore  # type: ignore[attr-defined]
from google.cloud.exceptions import GoogleCloudError
from app.models.smart_bucket import SmartBucket, SmartBucketRule
from app.models.item import Item
from datetime import datetime
import logging
from app.services.firestore_helpers import (
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


def _doc_to_smart_bucket(doc) -> SmartBucket:
    """Converts a Firestore document to a SmartBucket dataclass."""
    smart_bucket_data = doc.to_dict()
    smart_bucket_data["id"] = doc.id

    rules = []
    for rule_data in smart_bucket_data.get("rules", []):
        rules.append(SmartBucketRule(**rule_data))
    smart_bucket_data["rules"] = rules

    for date_field in ["createdAt", "updatedAt"]:
        if date_field in smart_bucket_data:
            raw_value = smart_bucket_data[date_field]
            normalised = normalise_timestamp(raw_value)
            if normalised is None and raw_value:
                logger.warning(
                    "Could not normalise date value '%s' for field '%s' in smart_bucket %s. Leaving as None.",
                    raw_value,
                    date_field,
                    doc.id,
                )
            smart_bucket_data[date_field] = normalised

    smart_bucket_fields = set(SmartBucket.__dataclass_fields__)
    filtered_data = {
        k: v for k, v in smart_bucket_data.items() if k in smart_bucket_fields
    }

    return SmartBucket(**filtered_data)


def list_smart_buckets() -> list[SmartBucket]:
    """Retrieves all smart buckets."""
    _require_db()
    try:
        smart_buckets_ref = db.collection(
            os.getenv("FIRESTORE_COLLECTION_SMART_BUCKETS", "smart_buckets")
        )
        docs = smart_buckets_ref.stream()
        return [_doc_to_smart_bucket(doc) for doc in docs]
    except GoogleCloudError as e:
        logger.error(f"Firestore error listing smart buckets: {e}", exc_info=True)
        raise FirestoreError("Failed to list smart buckets from Firestore.") from e


def create_smart_bucket(smart_bucket: SmartBucket) -> str:
    """Creates a new smart bucket in Firestore and returns its ID."""
    _require_db()
    try:
        smart_bucket_ref = db.collection(
            os.getenv("FIRESTORE_COLLECTION_SMART_BUCKETS")
        ).document()
        smart_bucket_data = smart_bucket.__dict__
        smart_bucket_data["createdAt"] = datetime.utcnow()
        smart_bucket_data["updatedAt"] = datetime.utcnow()
        smart_bucket_data["rules"] = [rule.__dict__ for rule in smart_bucket.rules]
        smart_bucket_ref.set(smart_bucket_data)
        return smart_bucket_ref.id
    except GoogleCloudError as e:
        logger.error(f"Firestore error creating smart bucket: {e}", exc_info=True)
        raise FirestoreError("Failed to create smart bucket in Firestore.") from e


def update_smart_bucket(smart_bucket_id: str, update_data: dict):
    """Updates a smart bucket document in Firestore."""
    _require_db()
    try:
        smart_bucket_ref = db.collection(
            os.getenv("FIRESTORE_COLLECTION_SMART_BUCKETS")
        ).document(smart_bucket_id)
        update_data["updatedAt"] = datetime.utcnow()
        if "rules" in update_data:
            update_data["rules"] = [rule.__dict__ for rule in update_data["rules"]]
        smart_bucket_ref.update(update_data)
    except GoogleCloudError as e:
        logger.error(
            f"Firestore error updating smart bucket {smart_bucket_id}: {e}",
            exc_info=True,
        )
        raise FirestoreError(
            f"Failed to update smart bucket {smart_bucket_id} in Firestore."
        ) from e


def evaluate_item(item: Item, rules: list[SmartBucketRule]) -> bool:
    """Evaluates an item against a list of smart bucket rules."""
    for rule in rules:
        item_value = getattr(item, rule.field, None)
        if item_value is None:
            return False

        if rule.operator == "contains":
            if rule.value.lower() not in item_value.lower():
                return False
        elif rule.operator == "not_contains":
            if rule.value.lower() in item_value.lower():
                return False
        elif rule.operator == "is":
            if item_value != rule.value:
                return False
        elif rule.operator == "is_not":
            if item_value == rule.value:
                return False
        else:
            return False  # Unknown operator

    return True
