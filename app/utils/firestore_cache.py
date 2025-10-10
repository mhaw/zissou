
from __future__ import annotations

import pickle
from datetime import datetime, timedelta, timezone

from flask_caching.backends.base import BaseCache
from google.cloud.firestore import Client


class FirestoreCache(BaseCache):
    """A Flask-Caching backend that uses Google Cloud Firestore.

    This backend stores each cache item as a document in a Firestore collection.
    It relies on a TTL policy being set on the 'expires_at' field of the
    collection to automatically purge expired items.

    The documents have the following structure:
    - value: A bytes object containing the pickled cached value.
    - expires_at: A timestamp indicating when the item should expire.
    """

    def __init__(
        self,
        client: Client,
        collection: str,
        default_timeout: int = 300,
    ):
        super().__init__(default_timeout)
        self._client = client
        self.collection = self._client.collection(collection)

    def get(self, key: str) -> t.Any | None:
        """Look up a key in the cache."""
        doc_ref = self.collection.document(key)
        doc = doc_ref.get()
        if not doc.exists:
            return None

        data = doc.to_dict()
        if not data:
            return None

        # Manually check for expiration as a fallback to the TTL policy
        expires_at = data.get("expires_at")
        if expires_at and datetime.now(timezone.utc) > expires_at:
            return None

        value = data.get("value")
        if value is None:
            return None

        try:
            return pickle.loads(value)
        except (pickle.UnpicklingError, TypeError):
            return None

    def set(self, key: str, value: t.Any, timeout: int | None = None) -> bool:
        """Add a new key/value to the cache."""
        timeout = self._get_timeout(timeout)
        if timeout <= 0:
            return True  # Treat as a no-op for non-positive timeouts

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=timeout)
        try:
            serialized_value = pickle.dumps(value, pickle.HIGHEST_PROTOCOL)
        except (pickle.PicklingError, TypeError):
            return False

        doc_ref = self.collection.document(key)
        doc_ref.set(
            {
                "value": serialized_value,
                "expires_at": expires_at,
            }
        )
        return True

    def add(self, key: str, value: t.Any, timeout: int | None = None) -> bool:
        """Add a key/value to the cache if it does not exist."""
        if self.has(key):
            return False
        return self.set(key, value, timeout)

    def delete(self, key: str) -> bool:
        """Delete a key from the cache."""
        doc_ref = self.collection.document(key)
        doc_ref.delete()
        return True

    def has(self, key: str) -> bool:
        """Check if a key exists in the cache."""
        return self.collection.document(key).get().exists

    def clear(self) -> bool:
        """Clear the entire cache. This is a destructive operation."""
        docs = self.collection.stream()
        for doc in docs:
            doc.reference.delete()
        return True

