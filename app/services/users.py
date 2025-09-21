import os
from google.cloud import firestore  # type: ignore[attr-defined]
from google.cloud.exceptions import GoogleCloudError
from app.models.user import User
from datetime import datetime
import logging
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

def _doc_to_user(doc) -> User:
    """Converts a Firestore document to a User dataclass."""
    user_data = doc.to_dict()
    user_data["id"] = doc.id

    for date_field in ["createdAt", "updatedAt"]:
        if date_field in user_data:
            raw_value = user_data[date_field]
            normalised = normalise_timestamp(raw_value)
            if normalised is None and raw_value:
                logger.warning(
                    "Could not normalise date value '%s' for field '%s' in user %s. Leaving as None.",
                    raw_value,
                    date_field,
                    doc.id,
                )
            user_data[date_field] = normalised

    user_fields = set(User.__dataclass_fields__)
    filtered_data = {k: v for k, v in user_data.items() if k in user_fields}

    return User(**filtered_data)

def get_user(user_id: str) -> User | None:
    """Retrieves a single user by their ID."""
    _require_db()
    try:
        user_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_USERS", "users")).document(user_id)
        doc = user_ref.get()
        if not doc.exists:
            return None
        return _doc_to_user(doc)
    except GoogleCloudError as e:
        logger.error(f"Firestore error retrieving user {user_id}: {e}")
        raise FirestoreError(f"Failed to retrieve user {user_id} from Firestore.") from e

def get_user_by_email(email: str) -> User | None:
    """Retrieves a user by their email address."""
    _require_db()
    try:
        users_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_USERS"))
        query = users_ref.where("email", "==", email).limit(1)
        docs = list(query.stream())
        if not docs:
            return None
        return _doc_to_user(docs[0])
    except GoogleCloudError as e:
        logger.error(f"Firestore error retrieving user by email {email}: {e}")
        raise FirestoreError(f"Failed to retrieve user by email {email} from Firestore.") from e

def create_user(user: User) -> str:
    """Creates a new user in Firestore and returns its ID."""
    _require_db()
    try:
        user_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_USERS")).document(user.id)
        user_data = user.__dict__
        user_data["createdAt"] = datetime.utcnow()
        user_data["updatedAt"] = datetime.utcnow()
        user_ref.set(user_data)
        return user_ref.id
    except GoogleCloudError as e:
        logger.error(f"Firestore error creating user: {e}")
        raise FirestoreError("Failed to create user in Firestore.") from e

def update_user(user_id: str, update_data: dict):
    """Updates a user document in Firestore."""
    _require_db()
    try:
        user_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_USERS")).document(user_id)
        update_data["updatedAt"] = datetime.utcnow()
        user_ref.update(update_data)
    except GoogleCloudError as e:
        logger.error(f"Firestore error updating user {user_id}: {e}")
        raise FirestoreError(f"Failed to update user {user_id} in Firestore.") from e
