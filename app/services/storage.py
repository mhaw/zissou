import logging
import os
import time
from urllib.parse import quote, urlparse

from google.cloud import storage
from google.cloud.exceptions import GoogleCloudError, NotFound

logger = logging.getLogger(__name__)

_TRANSIENT_STORAGE_STATUS = {429, 500, 502, 503, 504}


def _classify_storage_error(exc: GoogleCloudError) -> str:
    """Return 'transient', 'permanent', or 'unknown' for a storage error."""
    status = getattr(exc, "code", None)
    status_code = None
    if isinstance(status, int):
        status_code = status
    else:
        value = getattr(status, "value", None)
        if isinstance(value, int):
            status_code = value
    if status_code is not None:
        if status_code in _TRANSIENT_STORAGE_STATUS:
            return "transient"
        if 400 <= status_code < 500:
            return "permanent"
    message = str(exc).lower()
    if any(
        phrase in message
        for phrase in [
            "permission",
            "forbidden",
            "not authorized",
            "not found",
            "does not exist",
        ]
    ):
        return "permanent"
    return "unknown"


MAX_STORAGE_ATTEMPTS = int(os.getenv("STORAGE_UPLOAD_ATTEMPTS", "3"))
STORAGE_RETRY_INITIAL_BACKOFF = float(os.getenv("STORAGE_RETRY_INITIAL_BACKOFF", "0.5"))

_storage_client: storage.Client | None = None


class StorageError(Exception):
    """Custom exception for Google Cloud Storage related errors."""

    pass


def _get_storage_client() -> storage.Client:
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT"))
    return _storage_client


def upload_to_gcs(
    data: bytes, destination_blob_name: str, content_type: str = "audio/mpeg"
) -> str:
    """Uploads data to a GCS bucket and returns the public URL."""
    bucket_name = os.getenv("GCS_BUCKET")
    if not bucket_name:
        raise StorageError("GCS_BUCKET environment variable not set.")

    client = _get_storage_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)

    last_error: Exception | None = None
    for attempt in range(1, MAX_STORAGE_ATTEMPTS + 1):
        try:
            blob.upload_from_string(data, content_type=content_type)
            return get_public_url(destination_blob_name)
        except GoogleCloudError as exc:
            last_error = exc
            classification = _classify_storage_error(exc)
            if classification == "permanent":
                logger.error(
                    "Permanent Google Cloud Storage error on attempt %s/%s: %s",
                    attempt,
                    MAX_STORAGE_ATTEMPTS,
                    exc,
                )
                raise StorageError(f"Failed to upload to GCS: {exc}") from exc
            if attempt == MAX_STORAGE_ATTEMPTS:
                logger.error(
                    "Google Cloud Storage error during upload after %s attempts: %s",
                    attempt,
                    exc,
                )
                raise StorageError(f"Failed to upload to GCS: {exc}") from exc
            sleep_for = STORAGE_RETRY_INITIAL_BACKOFF * (2 ** (attempt - 1))
            if classification == "transient":
                logger.warning(
                    "Transient Google Cloud Storage error on attempt %s/%s: %s. Retrying in %.2fs",
                    attempt,
                    MAX_STORAGE_ATTEMPTS,
                    exc,
                    sleep_for,
                )
            else:
                logger.warning(
                    "Google Cloud Storage error on attempt %s/%s (treating as retryable): %s. Retrying in %.2fs",
                    attempt,
                    MAX_STORAGE_ATTEMPTS,
                    exc,
                    sleep_for,
                )
            time.sleep(sleep_for)
        except Exception as exc:
            last_error = exc
            logger.error("Unexpected error during GCS upload: %s", exc)
            raise StorageError(
                "An unexpected error occurred during GCS upload."
            ) from exc

    raise StorageError(f"Failed to upload to GCS: {last_error}")


def get_public_url(blob_name: str) -> str:
    """Generates a public URL for a GCS object without making it public."""
    bucket_name = os.getenv("GCS_BUCKET")
    return f"https://storage.googleapis.com/{bucket_name}/{quote(blob_name)}"


def extract_blob_name(file_url: str) -> str | None:
    """Return the bucket-relative blob name for a public or gs:// URL."""
    if not file_url:
        return None

    bucket_name = os.getenv("GCS_BUCKET")
    if not bucket_name:
        return None

    public_prefix = f"https://storage.googleapis.com/{bucket_name.strip('/')}/"
    if file_url.startswith(public_prefix):
        return file_url[len(public_prefix) :]

    parsed = urlparse(file_url)
    if parsed.scheme == "gs" and parsed.netloc == bucket_name:
        return parsed.path.lstrip("/")

    return None


def delete_blob(blob_name: str) -> None:
    """Delete a blob from the configured GCS bucket."""
    if not blob_name:
        return

    bucket_name = os.getenv("GCS_BUCKET")
    if not bucket_name:
        raise StorageError("GCS_BUCKET environment variable not set.")

    client = _get_storage_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    try:
        blob.delete()
    except NotFound:
        logger.info("Blob %s was already absent from bucket %s", blob_name, bucket_name)
    except GoogleCloudError as exc:
        logger.error("Google Cloud Storage error deleting %s: %s", blob_name, exc)
        raise StorageError(f"Failed to delete blob {blob_name}: {exc}") from exc
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.error("Unexpected error deleting blob %s: %s", blob_name, exc)
        raise StorageError(
            "An unexpected error occurred while deleting from GCS"
        ) from exc
