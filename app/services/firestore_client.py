from __future__ import annotations

import logging
import os
from typing import Optional

from google.cloud import firestore  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

_SKIP_FIRESTORE_INIT = os.getenv("ZISSOU_SKIP_FIRESTORE_INIT", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


class FirestoreError(Exception):
    """Custom exception for Firestore related errors."""

    pass


def _initialise_firestore_client() -> Optional[firestore.Client]:
    """Initialises the shared Firestore client used across services."""
    if _SKIP_FIRESTORE_INIT:
        logger.debug("Skipping Firestore client initialization (skip flag active).")
        return None

    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    try:
        return firestore.Client(project=project)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.critical("Failed to initialize Firestore client: %s", exc)
        return None


db: Optional[firestore.Client] = _initialise_firestore_client()


def get_client() -> Optional[firestore.Client]:
    """Return the shared Firestore client instance."""
    return db


def refresh_client(force: bool = False) -> Optional[firestore.Client]:
    """Reinitialise the Firestore client, primarily for tests."""
    global db
    if db is not None and not force:
        return db
    db = _initialise_firestore_client()
    return db
