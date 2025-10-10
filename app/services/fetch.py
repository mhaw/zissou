import logging
import os
import random
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Iterator, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from playwright.sync_api import sync_playwright, PlaywrightContextManager
except ModuleNotFoundError:
    sync_playwright: Optional[Callable[[], PlaywrightContextManager]] = None

logger = logging.getLogger(__name__)

USER_AGENT = os.getenv(
    "PARSER_USER_AGENT",
    "Mozilla/5.0 (compatible; ZissouBot/1.0; +https://github.com/zissou)",
)
REQUEST_TIMEOUT_SECONDS = float(os.getenv("PARSER_REQUEST_TIMEOUT_SECONDS", "10"))
FETCH_MAX_RETRIES = int(os.getenv("FETCH_MAX_RETRIES", "3"))
FETCH_BACKOFF_FACTOR = float(os.getenv("FETCH_BACKOFF_FACTOR", "0.5"))
FETCH_MAX_BACKOFF_SECONDS = float(os.getenv("FETCH_MAX_BACKOFF_SECONDS", "8"))
ACCEPT_LANG_OPTIONS = [
    value.strip()
    for value in os.getenv(
        "FETCH_ACCEPT_LANGUAGE_OPTIONS", "en-US,en;q=0.9|en-GB,en;q=0.8|en;q=0.7"
    ).split("|")
    if value.strip()
]
ACCEPT_HEADER_OPTIONS = [
    value.strip()
    for value in os.getenv(
        "FETCH_ACCEPT_OPTIONS",
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8|text/html;q=0.9,*/*;q=0.8",
    ).split("|")
    if value.strip()
]
HYBRID_REFERERS = [
    value.strip()
    for value in os.getenv(
        "FETCH_HYBRID_REFERERS",
        "https://news.google.com/,https://www.facebook.com/",
    ).split(",")
    if value.strip()
]
HYBRID_PROFILE_LIMIT = int(os.getenv("FETCH_HYBRID_PROFILE_LIMIT", "6"))
TRUNCATION_MIN_LENGTH = int(os.getenv("PARSER_TRUNCATION_MIN_LENGTH", "500"))
BLOCKING_PHRASES = [
    phrase.strip().lower()
    for phrase in os.getenv(
        "TRUNCATION_BLOCKING_PHRASES",
        "subscribe to read,sign in,sign up,log in to read,membership required",
    ).split(",")
    if phrase.strip()
]

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
TRANSIENT_STATUS_CODES.update(range(505, 600))

SleepFn = Callable[[float], None]


def _unique_profiles(profiles: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[tuple[str, str], ...]] = set()
    unique: list[dict[str, str]] = []
    for profile in profiles:
        key = tuple(sorted(profile.items()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(profile)
    return unique


def _compute_hybrid_profiles() -> list[dict[str, str]]:
    if HYBRID_PROFILE_LIMIT <= 0:
        return []

    languages = ACCEPT_LANG_OPTIONS or ["en-US,en;q=0.9"]
    referers = HYBRID_REFERERS

    profiles: list[dict[str, str]] = []

    for referer in referers:
        for language in languages:
            profiles.append({"Referer": referer, "Accept-Language": language})

    for language in languages:
        profiles.append({"Accept-Language": language})

    if referers:
        profiles.extend({"Referer": referer} for referer in referers)

    unique = _unique_profiles(profiles)
    return unique[:HYBRID_PROFILE_LIMIT]


_HYBRID_HEADER_PROFILES = _compute_hybrid_profiles()
_session_lock = threading.Lock()
_session: requests.Session | None = None


def _retry_adapter() -> HTTPAdapter:
    retry = Retry(
        total=3,
        backoff_factor=0.3,
        status_forcelist=sorted(TRANSIENT_STATUS_CODES),
        allowed_methods=("GET", "HEAD", "OPTIONS"),
    )
    return HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=32)


def _get_session() -> requests.Session:
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        sess = requests.Session()
        adapter = _retry_adapter()
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)
        _session = sess
    return _session


def _build_headers(user_agent: Optional[str]) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent or USER_AGENT,
        "Accept-Language": (
            random.choice(ACCEPT_LANG_OPTIONS)
            if ACCEPT_LANG_OPTIONS
            else "en-US,en;q=0.9"
        ),
        "Accept": (
            random.choice(ACCEPT_HEADER_OPTIONS)
            if ACCEPT_HEADER_OPTIONS
            else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
        "Cache-Control": "no-cache",
    }
    return headers


def _retry_wait_seconds(
    response: Optional[requests.Response], fallback: float
) -> float:
    retry_after = response.headers.get("Retry-After") if response else None
    if retry_after:
        retry_after = retry_after.strip()
        if retry_after.isdigit():
            return min(float(retry_after), FETCH_MAX_BACKOFF_SECONDS)
        try:
            parsed = parsedate_to_datetime(retry_after)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            delta = (parsed - datetime.now(timezone.utc)).total_seconds()
            if delta > 0:
                return min(delta, FETCH_MAX_BACKOFF_SECONDS)
        except (TypeError, ValueError):
            logger.debug("Failed to parse Retry-After header: %s", retry_after)
    return min(fallback, FETCH_MAX_BACKOFF_SECONDS)


def fetch_with_resilience(
    url: str,
    *,
    session: Optional[requests.Session] = None,
    timeout: Optional[float] = None,
    user_agent: Optional[str] = None,
    sleep: SleepFn = time.sleep,
    extra_headers: Optional[dict[str, str]] = None,
) -> dict:
    attempt = 0
    backoff = FETCH_BACKOFF_FACTOR
    session = session or _get_session()
    started = time.perf_counter()

    while True:
        attempt += 1
        headers = _build_headers(user_agent)
        if extra_headers:
            headers.update(extra_headers)
        try:
            logger.debug("Fetching %s (attempt %s)", url, attempt)
            response = session.get(
                url,
                headers=headers,
                timeout=timeout or REQUEST_TIMEOUT_SECONDS,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            logger.warning(
                "fetch.request_exception",
                extra={"url": url, "attempt": attempt, "error": str(exc)},
            )
            if attempt > FETCH_MAX_RETRIES:
                return {"error": f"Failed to fetch URL: {exc}"}
            wait = _retry_wait_seconds(None, backoff)
            logger.debug(
                "fetch.retry_sleep",
                extra={"url": url, "attempt": attempt, "sleep_seconds": wait},
            )
            sleep(wait)
            backoff = min(backoff * 2, FETCH_MAX_BACKOFF_SECONDS)
            continue

        if response.status_code in TRANSIENT_STATUS_CODES:
            logger.warning(
                "fetch.retryable_status",
                extra={
                    "url": url,
                    "status": response.status_code,
                    "attempt": attempt,
                },
            )
            if attempt > FETCH_MAX_RETRIES:
                return {"error": f"Failed to fetch URL: HTTP {response.status_code}"}
            wait = _retry_wait_seconds(response, backoff)
            logger.debug(
                "fetch.retry_sleep",
                extra={"url": url, "attempt": attempt, "sleep_seconds": wait},
            )
            sleep(wait)
            backoff = min(backoff * 2, FETCH_MAX_BACKOFF_SECONDS)
            continue

        if response.status_code >= 400:
            logger.error("Non-retriable status %s for %s", response.status_code, url)
            return {"error": f"Failed to fetch URL: HTTP {response.status_code}"}

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        payload = {
            "html": response.text,
            "final_url": response.url,
            "status_code": response.status_code,
            "response_headers": dict(response.headers),
            "request_headers": headers,
            "elapsed_ms": elapsed_ms,
        }
        logger.debug(
            "fetch.success",
            extra={
                "url": payload["final_url"],
                "status": payload["status_code"],
                "attempts": attempt,
                "elapsed_ms": elapsed_ms,
            },
        )
        return payload


def fetch_with_playwright(url: str, timeout: Optional[float] = None) -> dict:
    if sync_playwright is None:
        return {"error": "Playwright not installed"}

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, timeout=int((timeout or REQUEST_TIMEOUT_SECONDS) * 1000))
            html = page.content()
            final_url = page.url
            browser.close()
            return {"html": html, "final_url": final_url}
        except Exception as exc:
            return {"error": f"Playwright failed to fetch URL: {exc}"}


def is_likely_truncated(text: Optional[str]) -> bool:
    if not text:
        return True
    stripped = text.strip()
    if len(stripped) < TRUNCATION_MIN_LENGTH:
        return True
    lowered = stripped.lower()
    return any(phrase in lowered for phrase in BLOCKING_PHRASES)


def get_hybrid_header_profiles() -> list[dict[str, str]]:
    """Expose the deterministic header profiles used for hybrid retries."""
    return [profile.copy() for profile in _HYBRID_HEADER_PROFILES]


def hybrid_fetch_attempts(
    url: str,
    *,
    session: Optional[requests.Session] = None,
    timeout: Optional[float] = None,
    user_agent: Optional[str] = None,
    sleep: SleepFn = time.sleep,
) -> Iterator[tuple[dict[str, str], dict]]:
    if not _HYBRID_HEADER_PROFILES:
        return

    for profile in _HYBRID_HEADER_PROFILES:
        attempt_headers = profile.copy()
        yield attempt_headers, fetch_with_resilience(
            url,
            session=session,
            timeout=timeout,
            user_agent=user_agent,
            sleep=sleep,
            extra_headers=attempt_headers,
        )
