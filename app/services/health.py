import logging
from google.cloud import firestore, storage
from google.cloud.exceptions import GoogleCloudError

logger = logging.getLogger(__name__)


def check_firestore_health() -> tuple[bool, str]:
    """Checks the health of the Firestore connection."""
    try:
        db = firestore.Client()
        # A simple read operation to check the connection.
        list(db.collection("health_check").limit(1).stream())
        return True, "OK"
    except GoogleCloudError as e:
        logger.error(f"Firestore health check failed: {e}")
        return False, "Error"
    except Exception as e:
        logger.error(f"Unexpected error during Firestore health check: {e}")
        return False, "Error"


def check_gcs_health() -> tuple[bool, str]:
    """Checks the health of the GCS connection."""
    try:
        storage_client = storage.Client()
        # A simple operation to check the connection, like listing buckets.
        storage_client.list_buckets(max_results=1)
        return True, "OK"
    except GoogleCloudError as e:
        logger.error(f"GCS health check failed: {e}")
        return False, "Error"
    except Exception as e:
        logger.error(f"Unexpected error during GCS health check: {e}")
        return False, "Error"
