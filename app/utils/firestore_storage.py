from __future__ import annotations

import logging
import os
from time import time
from urllib.parse import urlparse

from google.api_core.exceptions import Aborted, GoogleAPICallError
from google.cloud import firestore
from limits.storage.base import Storage
from limits.storage.registry import SCHEMES

logger = logging.getLogger(__name__)


class FirestoreStorage(Storage):
    """Rate limit storage backend using Google Firestore."""

    def __init__(self, uri: str, **options: str) -> None:
        """
        Initializes the Firestore storage.
        URI can be in the format: firestore://[COLLECTION_NAME]
        If COLLECTION_NAME is not provided, it defaults to 'rate_limits'.
        """
        super().__init__(uri, **options)
        parsed_uri = urlparse(uri)
        self.collection_name = parsed_uri.hostname or "rate_limits"

        try:
            self.db = firestore.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT"))
            self.collection = self.db.collection(self.collection_name)
            # Check if the collection is accessible
            self.collection.limit(1).get()
            logger.info(
                "Firestore rate limit storage initialized for collection: %s",
                self.collection_name,
            )
        except GoogleAPICallError as e:
            logger.error("Failed to initialize Firestore for rate limiting: %s", e)
            self.db = None

    def check(self) -> bool:
        """Check if storage is healthy."""
        return self.db is not None

    @firestore.transactional
    def _get_and_update(self, transaction, key: str, amount: int, expiry: int):
        doc_ref = self.collection.document(key)
        snapshot = doc_ref.get(transaction=transaction)

        if not snapshot.exists:
            transaction.set(doc_ref, {"count": amount, "expiry": expiry})
            return amount, expiry

        data = snapshot.to_dict()
        current_count = data.get("count", 0)
        current_expiry = data.get("expiry", 0)

        if current_expiry < int(time()):
            transaction.set(doc_ref, {"count": amount, "expiry": expiry})
            return amount, expiry
        else:
            new_count = current_count + amount
            transaction.update(doc_ref, {"count": new_count})
            return new_count, current_expiry

    def incr(
        self, key: str, expiry: int, amount: int = 1, elastic_expiry: bool = False
    ) -> int:
        """
        Increment the counter for a given key.
        """
        if not self.check():
            return 0

        try:
            transaction = self.db.transaction()
            count, _ = self._get_and_update(
                transaction, key, amount, int(time()) + expiry
            )
            return count
        except (Aborted, GoogleAPICallError) as e:
            logger.warning("Firestore transaction failed during incr: %s", e)
            return expiry + 1

    def get(self, key: str) -> int:
        """
        Get the number of requests for a given key.
        """
        if not self.check():
            return 0
        doc = self.collection.document(key).get()
        if not doc.exists:
            return 0
        data = doc.to_dict()
        if data.get("expiry", 0) < int(time()):
            return 0
        return data.get("count", 0)

    def get_expiry(self, key: str) -> int:
        """
        Get the expiry time for a given key.
        """
        if not self.check():
            return 0
        doc = self.collection.document(key).get()
        if not doc.exists:
            return 0
        return doc.to_dict().get("expiry", 0)

    def reset(self) -> bool:
        """
        Delete all documents in the collection. Use with caution.
        """
        if not self.check():
            return False
        try:
            docs = self.collection.list_documents(page_size=500)
            for doc in docs:
                doc.delete()
            return True
        except GoogleAPICallError as e:
            logger.error("Failed to reset Firestore rate limit collection: %s", e)
            return False


# Register the storage backend with the name 'firestore'
SCHEMES["firestore"] = FirestoreStorage
