import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from cachetools import TTLCache

from app.services.firestore_helpers import normalise_timestamp
from app.services.exceptions import ArchiveTimeout, NetworkError, ParseError, TruncatedError
from urllib.parse import quote

logger = logging.getLogger(__name__)

ArchiveFetcher = Callable[[str], dict]
ExtractorFn = Callable[[str, str, Optional[str]], dict]
IsTruncatedFn = Callable[[Optional[str]], bool]

ARCHIVE_TODAY_BASE_URL = os.getenv("ARCHIVE_TODAY_BASE_URL", "https://archive.today")
WAYBACK_API_URL = os.getenv("WAYBACK_API_URL", "https://archive.org/wayback/available")
ARCHIVE_TIMEOUT_SECONDS = float(os.getenv("ARCHIVE_TIMEOUT", "8"))
ARCHIVE_CONCURRENCY = max(1, int(os.getenv("ARCHIVE_CONCURRENCY", "2")))
ARCHIVE_FAILURE_TTL_SECONDS = int(
    os.getenv("ARCHIVE_FAILURE_TTL_SECONDS", str(24 * 60 * 60))
)
ARCHIVE_FAILURE_COLLECTION = os.getenv(
    "FIRESTORE_ARCHIVE_FAILURE_COLLECTION", "archive_failures"
)

try:
    from google.cloud import firestore  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    firestore = None  # type: ignore[assignment]

_db = None
if firestore is not None:
    try:
        _db = firestore.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT"))
    except Exception as exc:  # pragma: no cover - Firestore optional
        logger.debug("Firestore client unavailable for archive caching: %s", exc)

_failure_cache = TTLCache(
    maxsize=int(os.getenv("ARCHIVE_FAILURE_CACHE_SIZE", "256")),
    ttl=ARCHIVE_FAILURE_TTL_SECONDS,
)
_semaphore = asyncio.Semaphore(ARCHIVE_CONCURRENCY)


def _log_archive_event(payload: dict[str, object]) -> None:
    try:
        logger.info("%s", json.dumps(payload, separators=(",", ":")))
    except TypeError:  # pragma: no cover - fallback if value not serialisable
        logger.info("archive_event %s", payload)


class AsyncArchiveRateLimiter:
    """Simple async rate limiter keyed by archive service."""

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._last_seen: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, key: str) -> None:
        async with self._lock:
            now = time.monotonic()
            last = self._last_seen.get(key)
            if last is not None:
                remaining = last + self._interval - now
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_seen[key] = time.monotonic()


ARCHIVE_REQUEST_INTERVAL_SECONDS = float(
    os.getenv("ARCHIVE_REQUEST_INTERVAL_SECONDS", "2")
)
_rate_limiter = AsyncArchiveRateLimiter(ARCHIVE_REQUEST_INTERVAL_SECONDS)


@dataclass
class ArchiveAttempt:
    label: str
    fetch_url: str


def _cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def _should_skip_archive(url: str) -> bool:
    """Returns True if this URL recently failed archive recovery."""
    if url in _failure_cache:
        _log_archive_event(
            {
                "event": "archive_skip",
                "url": url,
                "reason": "local_cache",
            }
        )
        return True

    if _db is None:
        return False

    doc_id = _cache_key(url)
    doc_ref = _db.collection(ARCHIVE_FAILURE_COLLECTION).document(doc_id)
    try:
        snapshot = doc_ref.get()
    except Exception as exc:  # pragma: no cover - requires Firestore
        logger.debug("Firestore archive cache lookup failed for %s: %s", url, exc)
        return False

    if not snapshot.exists:
        return False

    payload = snapshot.to_dict() or {}
    expires_at = normalise_timestamp(payload.get("expiresAt"))
    if not expires_at:
        return False

    now = datetime.now(timezone.utc)
    if expires_at > now:
        _failure_cache[url] = expires_at
        _log_archive_event(
            {
                "event": "archive_skip",
                "url": url,
                "reason": "firestore_cache",
            }
        )
        return True

    try:
        doc_ref.delete()
    except Exception as exc:  # pragma: no cover - requires Firestore
        logger.debug("Failed to purge expired archive cache doc for %s: %s", url, exc)
    return False


def _record_failure(url: str, reason: str) -> None:
    _log_archive_event(
        {
            "event": "archive_failure",
            "url": url,
            "reason": reason,
        }
    )
    _failure_cache[url] = datetime.now(timezone.utc)
    if _db is None:
        return
    doc_id = _cache_key(url)
    doc_ref = _db.collection(ARCHIVE_FAILURE_COLLECTION).document(doc_id)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ARCHIVE_FAILURE_TTL_SECONDS)
    payload = {
        "url": url,
        "reason": reason,
        "updatedAt": datetime.now(timezone.utc),
        "expiresAt": expires_at,
    }
    try:
        doc_ref.set(payload, merge=True)
    except Exception as exc:  # pragma: no cover - requires Firestore
        logger.debug("Failed to cache archive failure for %s: %s", url, exc)


def _clear_failure(url: str) -> None:
    _failure_cache.pop(url, None)
    if _db is None:
        return
    doc_id = _cache_key(url)
    doc_ref = _db.collection(ARCHIVE_FAILURE_COLLECTION).document(doc_id)
    try:
        doc_ref.delete()
    except Exception as exc:  # pragma: no cover - requires Firestore
        logger.debug("Failed to clear archive failure cache for %s: %s", url, exc)


def _enqueue_archive_snapshot(url: str, service: str) -> None:
    logger.info("Scheduling snapshot for %s via %s (stub)", url, service)


async def _fetch_archive_today(url: str, fetcher: ArchiveFetcher) -> Optional[dict]:
    snapshot_url = f"{ARCHIVE_TODAY_BASE_URL.rstrip('/')}/latest/{url}"
    await _rate_limiter.wait("archive.today")
    _log_archive_event(
        {
            "event": "archive_fetch",
            "service": "archive.today",
            "url": url,
            "stage": "request",
        }
    )
    async with _semaphore:
        result = await asyncio.to_thread(fetcher, snapshot_url)
    if result.get("error"):
        _enqueue_archive_snapshot(url, "archive.today")
        _log_archive_event(
            {
                "event": "archive_fetch",
                "service": "archive.today",
                "url": url,
                "status": "error",
                "error_type": NetworkError.__name__,
            }
        )
        return None
    _log_archive_event(
        {
            "event": "archive_fetch",
            "service": "archive.today",
            "url": url,
            "status": "retrieved",
        }
    )
    return result


async def _fetch_wayback(url: str, fetcher: ArchiveFetcher) -> Optional[dict]:
    api_url = f"{WAYBACK_API_URL.rstrip('/')}?url={quote(url, safe='')}"
    await _rate_limiter.wait("wayback_api")
    _log_archive_event(
        {
            "event": "archive_fetch",
            "service": "wayback",
            "url": url,
            "stage": "api",
        }
    )
    async with _semaphore:
        response = await asyncio.to_thread(fetcher, api_url)
    if response.get("error"):
        _log_archive_event(
            {
                "event": "archive_fetch",
                "service": "wayback",
                "url": url,
                "status": "error",
                "error_type": NetworkError.__name__,
            }
        )
        return None
    try:
        payload = json.loads(response.get("html", ""))
    except json.JSONDecodeError:
        _log_archive_event(
            {
                "event": "archive_fetch",
                "service": "wayback",
                "url": url,
                "status": "error",
                "error_type": ParseError.__name__,
            }
        )
        return None

    snapshots = payload.get("archived_snapshots", {})
    closest = snapshots.get("closest") if isinstance(snapshots, dict) else None
    snapshot_url = closest.get("url") if isinstance(closest, dict) else None
    if not snapshot_url:
        _enqueue_archive_snapshot(url, "wayback")
        _log_archive_event(
            {
                "event": "archive_fetch",
                "service": "wayback",
                "url": url,
                "status": "miss",
            }
        )
        return None

    await _rate_limiter.wait("wayback_snapshot")
    _log_archive_event(
        {
            "event": "archive_fetch",
            "service": "wayback",
            "url": url,
            "stage": "snapshot",
        }
    )
    async with _semaphore:
        snapshot = await asyncio.to_thread(fetcher, snapshot_url)
    if snapshot.get("error"):
        _log_archive_event(
            {
                "event": "archive_fetch",
                "service": "wayback",
                "url": url,
                "status": "error",
                "error_type": NetworkError.__name__,
            }
        )
        return None
    _log_archive_event(
        {
            "event": "archive_fetch",
            "service": "wayback",
            "url": url,
            "status": "retrieved",
        }
    )
    return snapshot


async def _attempt_archive(
    label: str,
    url: str,
    fetcher: ArchiveFetcher,
    extractor: ExtractorFn,
    is_truncated: IsTruncatedFn,
) -> Optional[dict]:
    if label == "archive.today":
        snapshot = await _fetch_archive_today(url, fetcher)
    elif label == "wayback":
        snapshot = await _fetch_wayback(url, fetcher)
    else:  # pragma: no cover - defensive
        logger.warning("Unknown archive service %s for %s", label, url)
        return None

    if not snapshot or snapshot.get("error"):
        return None

    processed = await asyncio.to_thread(
        extractor,
        snapshot.get("html", ""),
        url,
        snapshot.get("final_url"),
    )
    if processed.get("error"):
        _log_archive_event(
            {
                "event": "archive_process",
                "service": label,
                "url": url,
                "status": "error",
                "error_type": ParseError.__name__,
            }
        )
        return None
    if is_truncated(processed.get("text")):
        _log_archive_event(
            {
                "event": "archive_process",
                "service": label,
                "url": url,
                "status": "truncated",
                "error_type": TruncatedError.__name__,
            }
        )
        return None

    processed["fetched_via"] = label
    processed["archive_snapshot_url"] = snapshot.get("final_url")
    recovered_length = len((processed.get("text") or "").strip())
    _log_archive_event(
        {
            "event": "archive_recovered",
            "service": label,
            "url": url,
            "chars": recovered_length,
        }
    )
    return processed


async def recover_truncated_content_async(
    url: str,
    extracted_text: Optional[str],
    *,
    extractor: ExtractorFn,
    fetcher: ArchiveFetcher,
    is_truncated: IsTruncatedFn,
) -> Optional[dict]:
    if not is_truncated(extracted_text):
        return None

    if _should_skip_archive(url):
        return None

    services = ("archive.today", "wayback")
    start = time.perf_counter()
    _log_archive_event(
        {
            "event": "archive_recover_start",
            "url": url,
            "services": list(services),
        }
    )
    pending = {
        asyncio.create_task(
            _attempt_archive(service, url, fetcher, extractor, is_truncated)
        ): service
        for service in services
    }

    try:
        while pending:
            elapsed = time.perf_counter() - start
            remaining = ARCHIVE_TIMEOUT_SECONDS - elapsed
            if remaining <= 0:
                raise asyncio.TimeoutError
            done, _ = await asyncio.wait(
                pending.keys(),
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise asyncio.TimeoutError
            for task in done:
                service = pending.pop(task)
                try:
                    result = task.result()
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "Archive lookup via %s failed for %s: %s", service, url, exc
                    )
                    continue
                if result:
                    _clear_failure(url)
                    _log_archive_event(
                        {
                            "event": "archive_recover_finish",
                            "url": url,
                            "status": "success",
                            "service": service,
                        }
                    )
                    for future in pending:
                        future.cancel()
                    return result
        _record_failure(url, "no_snapshot")
        _log_archive_event(
            {
                "event": "archive_recover_finish",
                "url": url,
                "status": "failure",
            }
        )
        return None
    except asyncio.TimeoutError:
        _record_failure(url, "timeout")
        _log_archive_event(
            {
                "event": "archive_recover_timeout",
                "url": url,
                "error_type": ArchiveTimeout.__name__,
            }
        )
        return None
    finally:
        for task in pending:
            task.cancel()


def recover_truncated_content(
    url: str,
    extracted_text: Optional[str],
    *,
    extractor: ExtractorFn,
    fetcher: ArchiveFetcher,
    is_truncated: IsTruncatedFn,
) -> Optional[dict]:
    try:
        return asyncio.run(
            recover_truncated_content_async(
                url,
                extracted_text,
                extractor=extractor,
                fetcher=fetcher,
                is_truncated=is_truncated,
            )
        )
    except RuntimeError as exc:
        if "asyncio.run()" not in str(exc):
            raise
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(
                recover_truncated_content_async(
                    url,
                    extracted_text,
                    extractor=extractor,
                    fetcher=fetcher,
                    is_truncated=is_truncated,
                )
            )
        finally:
            asyncio.set_event_loop(None)
            loop.close()
