import logging
import os
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
        bucket_name = (os.getenv("GCS_BUCKET") or "").strip()
        if bucket_name:
            bucket = storage_client.lookup_bucket(bucket_name)
            if bucket is None:
                logger.error(
                    "Configured GCS bucket '%s' not found or inaccessible.", bucket_name
                )
                return False, "MissingBucket"
        else:
            # Fall back to a lightweight connectivity check.
            storage_client.list_buckets(max_results=1)
        return True, "OK"
    except GoogleCloudError as e:
        logger.error(f"GCS health check failed: {e}")
        return False, "Error"
    except Exception as e:
        logger.error(f"Unexpected error during GCS health check: {e}")
        return False, "Error"


def check_all_services() -> tuple[dict[str, str], bool]:
    """Checks the health of all downstream services and returns a summary.

    Returns:
        A tuple containing:
        - A dictionary with service names as keys and their status ('OK' or 'Error') as values.
        - A boolean indicating the overall health status (True if all services are OK, False otherwise).
    """
    service_checks = {
        "firestore": check_firestore_health,
        "gcs": check_gcs_health,
    }

    results = {}
    overall_healthy = True

    for service, check_func in service_checks.items():
        is_healthy, status = check_func()
        results[service] = status
        if not is_healthy:
            overall_healthy = False

    return results, overall_healthy
