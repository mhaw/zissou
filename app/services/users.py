import logging
import os
from datetime import datetime, timedelta, timezone

from flask import current_app
from google.cloud import firestore  # type: ignore[attr-defined]
from google.cloud.exceptions import GoogleCloudError

from app.models.user import User
from app.services.firestore_helpers import ensure_db_client

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


def get_user(user_id: str) -> User | None:
    """Retrieves a user by their ID."""
    try:
        user_ref = db.collection(
            os.getenv("FIRESTORE_COLLECTION_USERS", "users")
        ).document(user_id)
        doc = user_ref.get()
        if not doc.exists:
            return None
        return User.from_dict(doc.id, doc.to_dict())
    except GoogleCloudError as e:
        logger.error(f"Firestore error getting user {user_id}: {e}", exc_info=True)
        raise FirestoreError(f"Failed to get user {user_id}.") from e


@firestore.transactional
def get_or_create_user(transaction, decoded_token: dict) -> tuple[User, bool]:
    """Retrieves a user from Firestore by their UID from a decoded Firebase token
    within a transaction. If the user does not exist, a new user is created.

    Returns a tuple of (User, bool) where the boolean is True if the user was created.
    """
    uid = decoded_token["uid"]
    email = decoded_token.get("email", "").lower()
    users_collection = os.getenv("FIRESTORE_COLLECTION_USERS", "users")
    user_ref = db.collection(users_collection).document(uid)

    try:
        snapshot = user_ref.get(transaction=transaction)
        if snapshot.exists:
            return User.from_dict(snapshot.id, snapshot.to_dict()), False

        # New users always start as 'member'. Admin status can be granted later.
        role = "member"

        new_user = User(
            id=uid,
            email=email,
            name=decoded_token.get("name") or "",
            role=role,
            createdAt=datetime.now(timezone.utc),
            updatedAt=datetime.now(timezone.utc),
        )
        transaction.set(user_ref, new_user.to_dict())
        return new_user, True
    except GoogleCloudError as e:
        logger.error(
            f"Firestore transaction error in get_or_create_user for uid {uid}: {e}",
            exc_info=True,
        )
        raise FirestoreError(f"Failed to get or create user {uid}.") from e


def create_user(user: User):
    """Creates a new user document in Firestore."""
    try:
        user_ref = db.collection(
            os.getenv("FIRESTORE_COLLECTION_USERS", "users")
        ).document(user.id)
        user_data = user.to_dict()
        user_data["createdAt"] = datetime.now(timezone.utc)
        user_data["updatedAt"] = datetime.now(timezone.utc)
        user_ref.set(user_data)
        return user_ref.id
    except GoogleCloudError as e:
        logger.error(f"Firestore error creating user: {e}", exc_info=True)
        raise FirestoreError("Failed to create user in Firestore.") from e


def update_user(user_id: str, update_data: dict):
    """Updates a user document in Firestore."""
    _require_db()
    try:
        user_ref = db.collection(
            os.getenv("FIRESTORE_COLLECTION_USERS", "users")
        ).document(user_id)
        update_data["updatedAt"] = datetime.now(timezone.utc)
        user_ref.update(update_data)
    except GoogleCloudError as e:
        logger.error(f"Firestore error updating user {user_id}: {e}", exc_info=True)
        raise FirestoreError(f"Failed to update user {user_id} in Firestore.") from e


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


def get_user_count() -> int:
    """Returns the total number of users."""
    _require_db()
    try:
        users_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_USERS", "users"))
        return _run_count(users_ref)
    except GoogleCloudError as e:
        logger.error(f"Firestore error getting user count: {e}", exc_info=True)
        return 0


def get_recent_user_count(hours: int = 24) -> int:
    """Returns the number of users created in the last N hours."""
    _require_db()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        users_ref = db.collection(os.getenv("FIRESTORE_COLLECTION_USERS", "users"))
        query = users_ref.where(filter=firestore.FieldFilter("createdAt", ">=", cutoff))
        return _run_count(query)
    except (GoogleCloudError, ValueError) as e:
        logger.error(f"Firestore error getting recent user count: {e}", exc_info=True)
        return 0


def delete_user(user_id: str):
    """Deletes a user from Firestore and Firebase Authentication."""
    _require_db()
    try:
        # First, delete the user's document from Firestore
        user_ref = db.collection(
            os.getenv("FIRESTORE_COLLECTION_USERS", "users")
        ).document(user_id)
        user_ref.delete()
        logger.info(f"Deleted user document {user_id} from Firestore.")

        # Then, delete the user from Firebase Authentication
        from firebase_admin import auth

        auth.delete_user(user_id)
        logger.info(f"Deleted user {user_id} from Firebase Authentication.")

        # Enqueue a task to delete all items associated with this user
        from app.services import (
            tasks as tasks_service,
        )  # Local import to avoid circular deps

        tasks_service.create_delete_user_items_task(user_id)
        logger.info(f"Enqueued item deletion task for user {user_id}.")

    except GoogleCloudError as e:
        logger.error(f"Firestore error deleting user {user_id}: {e}", exc_info=True)
        raise FirestoreError(f"Failed to delete user {user_id} from Firestore.") from e
    except auth.UserNotFoundError:
        logger.warning(
            f"User {user_id} not found in Firebase Authentication, but deleting from Firestore anyway."
        )
    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {e}", exc_info=True)
        raise FirestoreError(
            f"An unexpected error occurred while deleting user {user_id}."
        ) from e
