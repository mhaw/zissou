import os
import logging
from datetime import datetime
from flask import g

from google.cloud import firestore
from google.cloud.exceptions import GoogleCloudError

from app.services.firestore_helpers import ensure_db_client

logger = logging.getLogger(__name__)

AUDIT_COLLECTION = os.getenv("FIRESTORE_COLLECTION_AUDIT", "audit")

try:
    db = firestore.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT"))
except Exception as exc:  # pragma: no cover - defensive logging
    logger.critical("Failed to initialize Firestore client for audit service: %s", exc)
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


def log_event(event_type: str, user_id: str, details: dict | None = None):
    try:
        audit_ref = db.collection(
            os.getenv("FIRESTORE_COLLECTION_AUDIT", "audit")
        ).document()
        event_data = {
            "eventType": event_type,
            "userId": user_id,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "details": details or {},
        }
        audit_ref.set(event_data)
    except GoogleCloudError as e:
        logger.error(f"Firestore error logging audit event: {e}", exc_info=True)
        raise FirestoreError("Failed to log audit event to Firestore.") from e


def log_admin_action(
    action: str, target_id: str | None = None, details: dict | None = None
):
    """Logs an action performed by an admin user."""
    _require_db()

    user = g.get("user")
    if not user or user.get("role") != "admin":
        logger.warning("Attempted to log admin action for non-admin user.")
        return

    try:
        audit_ref = db.collection(AUDIT_COLLECTION).document()
        log_entry = {
            "admin_uid": user.get("uid"),
            "admin_email": user.get("email"),
            "action": action,
            "target_id": target_id,
            "timestamp": datetime.utcnow(),
            "details": details or {},
        }
        audit_ref.set(log_entry)
    except Exception as e:
        logger.error(f"Failed to log admin action: {e}", exc_info=True)
