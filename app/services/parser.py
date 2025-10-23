import inspect
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Tuple
from urllib.parse import urlparse

import trafilatura
from cachetools import TTLCache

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    BeautifulSoup = None  # type: ignore[assignment]

try:
    from newspaper import Article, Config
except (
    ModuleNotFoundError
):  # pragma: no cover - optional dependency for offline test runs
    Article: Optional[type] = None  # type: ignore[assignment]
    Config: Optional[type] = None  # type: ignore[assignment]

try:
    from goose3 import Goose
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    Goose = None  # type: ignore[assignment]

try:
    from readability import Document
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    Document = None  # type: ignore[assignment]

from trafilatura.settings import use_config  # type: ignore[import-untyped]

try:
    from trafilatura.settings import use_browser_config  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - optional in older versions
    use_browser_config = None  # type: ignore[assignment]

import structlog
from app.utils.logging_config import setup_logging
from app.utils.text_cleaner import clean_text
from app.services.fetch import (
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
    fetch_with_resilience,
    fetch_with_playwright,
    hybrid_fetch_attempts,
    is_likely_truncated,
)
from app.services.archive_utils import recover_truncated_content
from app.services.exceptions import ParseError, TruncatedError


WORDS_PER_MINUTE = 200


def calculate_reading_time(text: str, words_per_minute: int = WORDS_PER_MINUTE) -> int:
    """Calculates the estimated reading time in minutes for a given text."""
    words = text.split()
    num_words = len(words)
    reading_time = num_words / words_per_minute
    return max(1, int(round(reading_time)))


setup_logging()


logger = structlog.get_logger(__name__)

ExtractorFn = Callable[[str, str, Optional[str]], dict[str, Any]]

ENGINE_SUCCESS_THRESHOLD = int(os.getenv("EXTRACTOR_SUCCESS_THRESHOLD", "500"))
HEURISTIC_MIN_PARAGRAPH_CHARS = int(os.getenv("HEURISTIC_MIN_PARAGRAPH_CHARS", "40"))
HEURISTIC_MIN_TOTAL_CHARS = int(os.getenv("HEURISTIC_MIN_TOTAL_CHARS", "500"))
HEURISTIC_SKIP_PHRASES = [
    phrase.strip().lower()
    for phrase in os.getenv(
        "HEURISTIC_SKIP_PHRASES",
        "copyright,all rights reserved,photo,advertisement,sign up",
    ).split(",")
    if phrase.strip()
]
PLAINTEXT_MIN_TOTAL_CHARS = int(os.getenv("PLAINTEXT_MIN_TOTAL_CHARS", "280"))
FALLBACK_MIN_LENGTH = int(os.getenv("FALLBACK_MIN_LENGTH", "1500"))
DOMAIN_PREFERENCE_TTL_SECONDS = int(
    os.getenv("EXTRACTOR_DOMAIN_PREFERENCE_TTL_SECONDS", str(6 * 60 * 60))
)

_ENGINE_ATTEMPTS: Counter[str] = Counter()
_ENGINE_SUCCESSES: Counter[str] = Counter()
_ENGINE_FAILURES: Counter[str] = Counter()
_ENGINE_WINS: Counter[str] = Counter()

ENGINE_PIPELINE_ORDER: Tuple[str, ...] = (
    "trafilatura",
    "newspaper3k",
    "goose3",
    "readability",
    "soup_heuristic",
    "plaintext_fallback",
)
ARCHIVE_SKIP_PARSERS = {"trafilatura", "newspaper3k"}

_DOMAIN_EXTRACTOR_OVERRIDES: dict[str, Tuple[str, ...]] = {
    "nytimes.com": ("trafilatura", "newspaper3k", "goose3", "readability"),
    "theguardian.com": ("newspaper3k", "trafilatura", "goose3", "readability"),
    "theatlantic.com": ("trafilatura", "goose3", "newspaper3k", "readability"),
    "newyorker.com": ("trafilatura", "goose3", "readability", "newspaper3k"),
    "wired.com": ("newspaper3k", "trafilatura", "goose3", "readability"),
}

_DOMAIN_SUCCESS_CACHE: TTLCache[str, str] = TTLCache(
    maxsize=int(os.getenv("EXTRACTOR_DOMAIN_CACHE_SIZE", "256")),
    ttl=DOMAIN_PREFERENCE_TTL_SECONDS,
)


@dataclass(frozen=True)
class ExtractorStrategy:
    name: str
    extractor: ExtractorFn
    extractor_attr: Optional[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        attr_name = getattr(self.extractor, "__name__", None)
        object.__setattr__(self, "extractor_attr", attr_name)
        _log_extractor_event(
            {
                "event": "extractor_registered",
                "name": self.name,
                "module": getattr(self.extractor, "__module__", "unknown"),
                "defined": callable(self.extractor),
            }
        )

    def run(
        self, target_url: str, html: str, *, source_url: Optional[str] = None
    ) -> dict[str, Any]:
        extractor_fn = self.extractor
        if self.extractor_attr:
            module = sys.modules.get(__name__)
            candidate = getattr(module, self.extractor_attr, None)
            if callable(candidate):
                extractor_fn = candidate
        return extractor_fn(target_url, html, source_url)


class ExtractorRegistry:
    def __init__(self, strategies: Iterable[ExtractorStrategy]):
        self._strategies: dict[str, ExtractorStrategy] = {
            strategy.name: strategy for strategy in strategies
        }

    def get(self, name: str) -> Optional[ExtractorStrategy]:
        return self._strategies.get(name)

    def ordered(self, names: Iterable[str]) -> list[ExtractorStrategy]:
        seen: set[str] = set()
        ordered: list[ExtractorStrategy] = []
        for name in names:
            if name in seen:
                continue
            strategy = self.get(name)
            if strategy is None:
                continue
            ordered.append(strategy)
            seen.add(name)
        return ordered

    def all(self) -> list[ExtractorStrategy]:
        return list(self._strategies.values())


@dataclass
class ArticleParseResult:
    title: str
    author: str
    published_at: Optional[str]
    image_url: Optional[str]
    text: str
    reading_time: int = 0


try:
    _TRAFILATURA_METADATA_SUPPORTS_CONFIG = (
        "config" in inspect.signature(trafilatura.extract_metadata).parameters
    )
except (ValueError, TypeError, AttributeError):
    _TRAFILATURA_METADATA_SUPPORTS_CONFIG = False


def _extract_trafilatura_metadata(downloaded: str, url: str, config) -> object | None:
    metadata = None
    if _TRAFILATURA_METADATA_SUPPORTS_CONFIG:
        try:
            metadata = trafilatura.extract_metadata(downloaded, config=config)
        except Exception as exc:  # pragma: no cover - defensive for odd installs
            # structlog treats the first positional argument as the ``event`` field; keep it keyword-only.
            logger.warning(
                event="extractor_metadata_failure",
                operation="extractor.metadata",
                engine="trafilatura",
                mode="config",
                error=str(exc),
            )
            metadata = None
        if metadata:
            return metadata
    try:
        return trafilatura.extract_metadata(downloaded, default_url=url)
    except Exception as exc:  # pragma: no cover - defensive for odd installs
        # structlog treats the first positional argument as the ``event`` field; keep it keyword-only.
        logger.warning(
            event="extractor_metadata_failure",
            operation="extractor.metadata",
            engine="trafilatura",
            mode="default",
            error=str(exc),
        )
        return None


def _initialise_trafilatura_config():
    config = None
    if callable(use_browser_config):
        try:
            config = use_browser_config()  # type: ignore[call-arg]
        except Exception as exc:  # pragma: no cover - optional dependency
            # structlog treats the first positional argument as the ``event`` field; keep it keyword-only.
            logger.warning(
                event="extractor_trafilatura_config_failure",
                operation="extractor.trafilatura",
                mode="browser",
                error=str(exc),
            )
            config = None
    if config is None:
        try:
            config = use_config()
        except Exception as exc:  # pragma: no cover - defensive guard
            # structlog treats the first positional argument as the ``event`` field; keep it keyword-only.
            logger.warning(
                event="extractor_trafilatura_config_failure",
                operation="extractor.trafilatura",
                mode="default",
                error=str(exc),
            )
            config = None
    if config is None:
        return None

    # Apply runtime hints where supported.
    try:
        config.set("DEFAULT", "USER_AGENT", USER_AGENT)
    except Exception:  # pragma: no cover - config may not support attributes
        pass
    try:
        config.set("DEFAULT", "EXTRACTION_TIMEOUT", str(REQUEST_TIMEOUT_SECONDS))
    except Exception:  # pragma: no cover - config may not support attributes
        pass

    return config


def _extract_with_trafilatura(
    url: str, html: str, source_url: Optional[str] = None
) -> dict[str, Any]:
    downloaded = html or ""
    config = _initialise_trafilatura_config()

    if not downloaded:
        try:
            downloaded = trafilatura.fetch_url(url, config=config)
        except Exception as exc:  # pragma: no cover - defensive network guard
            return {
                "error": f"Unable to fetch URL: {exc}",
                "resolved_url": url,
                "source_url": source_url or url,
            }

    if not downloaded:
        return {
            "error": "Unable to fetch URL",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    try:
        text = trafilatura.extract(
            downloaded,
            url=url,
            config=config,
            include_links=False,
            include_comments=False,
            favor_precision=True,
        )
    except Exception as exc:  # pragma: no cover - unexpected parsing issue
        return {
            "error": f"Trafilatura extraction failed: {exc}",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    cleaned_text = clean_text(text or "")
    if not cleaned_text:
        return {
            "error": "Trafilatura returned empty content.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    metadata = _extract_trafilatura_metadata(downloaded, url, config)

    def _meta_value(candidate: object, *keys: str) -> Optional[str]:
        if not candidate:
            return None
        for key in keys:
            if isinstance(candidate, dict):
                value = candidate.get(key)
            else:
                value = getattr(candidate, key, None)
            if value:
                if hasattr(value, "isoformat"):
                    return value.isoformat()  # type: ignore[return-value]
                return str(value)
        return None

    title = _meta_value(metadata, "title", "headline") or "Untitled"
    author = _meta_value(metadata, "author") or "Unknown"
    published = _meta_value(metadata, "date", "published_date")
    image_url = _meta_value(metadata, "image_url", "image")

    return {
        "title": title,
        "author": author,
        "text": cleaned_text,
        "source_url": source_url or url,
        "resolved_url": url,
        "published_date": published,
        "image_url": image_url,
        "parser": "trafilatura",
    }


def _record_attempt(engine: str) -> None:
    _ENGINE_ATTEMPTS[engine] += 1


def _record_success(engine: str) -> None:
    _ENGINE_SUCCESSES[engine] += 1


def _record_failure(engine: str) -> None:
    _ENGINE_FAILURES[engine] += 1


def _record_win(engine: str) -> None:
    _ENGINE_WINS[engine] += 1


def _build_metrics_snapshot(
    winner: Optional[str], last_engine: Optional[str] = None
) -> dict:
    snapshot: dict[str, Any] = {}
    for engine in ENGINE_PIPELINE_ORDER:
        attempts = _ENGINE_ATTEMPTS.get(engine, 0)
        wins = _ENGINE_WINS.get(engine, 0)
        successes = _ENGINE_SUCCESSES.get(engine, 0)
        failures = _ENGINE_FAILURES.get(engine, 0)
        win_rate = (wins / attempts) if attempts else 0.0
        snapshot[engine] = {
            "attempts": attempts,
            "successes": successes,
            "failures": failures,
            "wins": wins,
            "win_rate": round(win_rate, 3) if attempts else 0.0,
        }
    if winner:
        snapshot["winner"] = winner
    if last_engine and last_engine != winner:
        snapshot["last_engine"] = last_engine
    return snapshot


def get_extractor_metrics() -> dict:
    """Expose current extractor performance counters for diagnostics."""
    return _build_metrics_snapshot(None)


def _normalise_domain(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _domain_override_for(domain: str) -> Optional[Tuple[str, ...]]:
    if domain in _DOMAIN_EXTRACTOR_OVERRIDES:
        return _DOMAIN_EXTRACTOR_OVERRIDES[domain]
    for key, value in _DOMAIN_EXTRACTOR_OVERRIDES.items():
        if domain.endswith(key):
            return value
    return None


def _merge_pipeline_order(priority: Iterable[str], base: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for name in list(priority) + list(base):
        if EXTRACTOR_REGISTRY.get(name) is None:
            continue
        if name in seen:
            continue
        merged.append(name)
        seen.add(name)
    return merged


def _build_pipeline_for(url: str) -> Tuple[ExtractorStrategy, ...]:
    domain = _normalise_domain(url)
    base_order = list(ENGINE_PIPELINE_ORDER)
    override = _domain_override_for(domain)
    if override:
        base_order = _merge_pipeline_order(override, base_order)
    last_success = _DOMAIN_SUCCESS_CACHE.get(domain)
    if last_success:
        base_order = _merge_pipeline_order((last_success,), base_order)

    pipeline: list[ExtractorStrategy] = []
    for name in base_order:
        strategy = EXTRACTOR_REGISTRY.get(name)
        if strategy is not None:
            pipeline.append(strategy)
    return tuple(pipeline)


def _remember_domain_success(domain: str, extractor: Optional[str]) -> None:
    if not domain or not extractor:
        return
    _DOMAIN_SUCCESS_CACHE[domain] = extractor


def _log_extractor_event(payload: dict[str, Any]) -> None:
    event_name = payload.get("event") or "extractor_event"
    details = {k: v for k, v in payload.items() if k != "event"}
    try:
        message = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        serialised = {k: str(v) for k, v in payload.items()}
        message = json.dumps(serialised, separators=(",", ":"), ensure_ascii=False)
    logging.getLogger(__name__).info(message)
    # structlog treats the first positional argument as the ``event`` field; pass it via keyword only.
    logger.info(event=event_name, operation="extractor.event", **details)


def extract_text(url: str) -> dict:
    """Extract the main article content with resilient fetching and recovery."""
    use_playwright = os.getenv("ENABLE_PLAYWRIGHT", "false").lower() in (
        "true",
        "1",
        "yes",
    )

    if use_playwright:
        fetch_result = fetch_with_playwright(url)
    else:
        fetch_result = fetch_with_resilience(url, user_agent=USER_AGENT)

    if fetch_result.get("error"):
        return fetch_result

    html = fetch_result.get("html", "")
    resolved_url = fetch_result.get("final_url") or url

    parsed = _process_html(html, url, resolved_url)
    if parsed.get("error"):
        return parsed

    parsed.setdefault("fetched_via", "playwright" if use_playwright else "direct")

    text = (parsed.get("text") or "").strip()
    parser_name = parsed.get("parser")
    text_length = len(text)

    if parser_name in ARCHIVE_SKIP_PARSERS and text_length >= FALLBACK_MIN_LENGTH:
        parsed["archive_attempted"] = False
        return parsed

    if not is_likely_truncated(text):
        parsed["archive_attempted"] = False
        return parsed

    baseline_length = text_length
    _log_extractor_event(
        {
            "event": "content_truncated",
            "url": resolved_url or url,
            "domain": _normalise_domain(resolved_url or url),
            "parser": parser_name,
            "chars": baseline_length,
            "error_type": TruncatedError.__name__,
        }
    )
    parsed["archive_attempted"] = True

    hybrid = _attempt_hybrid_refetch(
        resolved_url or url,
        origin_url=url,
        baseline_length=baseline_length,
    )
    if hybrid:
        hybrid["archive_attempted"] = False
        _log_extractor_event(
            {
                "event": "hybrid_retry_success",
                "url": resolved_url or url,
                "domain": _normalise_domain(resolved_url or url),
                "parser": hybrid.get("parser"),
                "chars": len((hybrid.get("text") or "").strip()),
            }
        )
        return hybrid

    recovered = recover_truncated_content(
        url,
        parsed.get("text"),
        extractor=_process_html,
        fetcher=lambda target: fetch_with_resilience(target, user_agent=USER_AGENT),
        is_truncated=is_likely_truncated,
    )
    if recovered:
        recovered["archive_attempted"] = True
        return recovered

    return parsed


def _process_html(
    html: str, origin_url: str, resolved_url: Optional[str] = None
) -> dict:
    target_url = resolved_url or origin_url
    domain = _normalise_domain(target_url)
    errors: list[str] = []
    best_result: Optional[dict] = None
    best_length = 0
    best_truncated = False
    winning_engine: Optional[str] = None

    pipeline = _build_pipeline_for(target_url)
    pipeline_attempts: list[dict[str, Any]] = []

    for strategy in pipeline:
        engine_name = strategy.name
        _record_attempt(engine_name)
        attempt_started = time.perf_counter()
        attempt_entry: dict[str, Any] = {
            "engine": engine_name,
            "url": target_url,
        }
        pipeline_attempts.append(attempt_entry)
        try:
            result = strategy.run(target_url, html, source_url=origin_url)
        except Exception as exc:  # pragma: no cover - defensive guard
            elapsed_ms = int((time.perf_counter() - attempt_started) * 1000)
            _record_failure(engine_name)
            errors.append(f"{engine_name}: {exc}")
            attempt_entry.update(
                status="exception",
                error_type=exc.__class__.__name__,
                elapsed_ms=elapsed_ms,
            )
            # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
            logger.warning(
                event="extractor_attempt",
                operation="extractor.attempt",
                engine=engine_name,
                url=target_url,
                status="exception",
                error_type=exc.__class__.__name__,
                elapsed_ms=elapsed_ms,
            )
            continue

        elapsed_ms = int((time.perf_counter() - attempt_started) * 1000)

        if result.get("error"):
            _record_failure(engine_name)
            error_message = str(result["error"])
            errors.append(f"{engine_name}: {error_message}")
            attempt_entry.update(
                status="error",
                error_type=ParseError.__name__,
                elapsed_ms=elapsed_ms,
            )
            # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
            logger.warning(
                event="extractor_attempt",
                operation="extractor.attempt",
                engine=engine_name,
                url=target_url,
                status="error",
                error_type=ParseError.__name__,
                elapsed_ms=elapsed_ms,
            )
            continue

        text = (result.get("text") or "").strip()
        text_length = len(text)
        truncated = is_likely_truncated(text) if text_length else False

        if text_length >= ENGINE_SUCCESS_THRESHOLD and not truncated:
            _record_success(engine_name)
            attempt_entry.update(
                status="success",
                chars=text_length,
                truncated=False,
                elapsed_ms=elapsed_ms,
            )
            # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
            logger.info(
                event="extractor_attempt",
                operation="extractor.attempt",
                engine=engine_name,
                url=target_url,
                status="success",
                chars=text_length,
                truncated=False,
                elapsed_ms=elapsed_ms,
            )
            best_result = result
            best_length = text_length
            best_truncated = False
            winning_engine = result.get("parser") or engine_name
            break

        _record_failure(engine_name)

        if text_length == 0:
            status = "empty"
        elif truncated:
            status = "truncated"
        elif text_length >= ENGINE_SUCCESS_THRESHOLD:
            status = "ok"
        else:
            status = "short"

        attempt_entry.update(
            status=status,
            chars=text_length,
            truncated=truncated if text_length else None,
            elapsed_ms=elapsed_ms,
        )
        # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
        logger.info(
            event="extractor_attempt",
            operation="extractor.attempt",
            engine=engine_name,
            url=target_url,
            status=status,
            chars=text_length,
            truncated=truncated if text_length else None,
            elapsed_ms=elapsed_ms,
        )

        if text_length == 0:
            continue

        if text_length > best_length:
            best_result = result
            best_length = text_length
            best_truncated = truncated
            winning_engine = result.get("parser") or engine_name

    if best_result and winning_engine:
        full_success = best_length >= ENGINE_SUCCESS_THRESHOLD and not best_truncated
        metrics_winner = winning_engine if full_success else None
        pipeline_status = "success" if full_success else "fallback"
        _log_extractor_event(
            {
                "event": "extractor_pipeline",
                "url": target_url,
                "domain": domain,
                "status": pipeline_status,
                "winner": winning_engine,
                "winner_chars": best_length,
                "attempts": pipeline_attempts,
            }
        )
        if full_success:
            _record_win(winning_engine)
            _remember_domain_success(domain, winning_engine)
            # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
            logger.info(
                event="extractor_pipeline_status",
                operation="extractor.pipeline",
                status="success",
                engine=winning_engine,
                url=target_url,
                domain=domain,
                chars=best_length,
            )
        else:
            # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
            logger.info(
                event="extractor_pipeline_status",
                operation="extractor.pipeline",
                status="fallback",
                engine=winning_engine,
                url=target_url,
                domain=domain,
                chars=best_length,
            )

        best_result.setdefault("parser", winning_engine)
        best_result.setdefault("resolved_url", target_url)
        best_result.setdefault("source_url", origin_url)
        best_result["extractor_metrics"] = _build_metrics_snapshot(
            metrics_winner, winning_engine
        )
        best_result["reading_time"] = calculate_reading_time(
            best_result.get("text", "")
        )
        return best_result

    error_message = errors[0] if errors else "No extractor produced text."
    # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
    logger.error(
        event="extractor_pipeline_error",
        operation="extractor.pipeline",
        url=target_url,
        domain=domain,
        status="failure",
        error=error_message,
    )
    _log_extractor_event(
        {
            "event": "extractor_pipeline",
            "url": target_url,
            "domain": domain,
            "status": "failure",
            "attempts": pipeline_attempts,
            "error_message": error_message,
        }
    )
    return {
        "error": error_message,
        "resolved_url": target_url,
        "source_url": origin_url,
        "extractor_metrics": _build_metrics_snapshot(None),
    }


def _attempt_hybrid_refetch(
    fetch_url: str,
    origin_url: str,
    baseline_length: int,
) -> Optional[dict]:
    # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
    logger.info(
        event="extractor_hybrid_retry",
        operation="extractor.hybrid_retry",
        url=fetch_url,
        baseline_chars=baseline_length,
    )

    best_candidate: Optional[dict] = None
    best_length = baseline_length

    for profile, fetch_result in hybrid_fetch_attempts(
        fetch_url, user_agent=USER_AGENT
    ):
        if not fetch_result or fetch_result.get("error"):
            continue

        candidate = _process_html(
            fetch_result.get("html", ""), origin_url, fetch_result.get("final_url")
        )

        if candidate.get("error"):
            continue

        text = (candidate.get("text") or "").strip()
        text_length = len(text)
        if text_length <= best_length:
            continue
        if is_likely_truncated(text):
            continue

        candidate.setdefault("fetched_via", "direct-hybrid")
        candidate["fetch_profile"] = profile
        best_candidate = candidate
        best_length = text_length

        if best_length >= ENGINE_SUCCESS_THRESHOLD:
            break

    if best_candidate:
        # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
        logger.info(
            event="extractor_hybrid_retry",
            operation="extractor.hybrid_retry",
            url=fetch_url,
            baseline_chars=baseline_length,
            improved_chars=best_length,
            status="success",
        )
        return best_candidate

    # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
    logger.info(
        event="extractor_hybrid_retry",
        operation="extractor.hybrid_retry",
        url=fetch_url,
        baseline_chars=baseline_length,
        status="unchanged",
    )
    return None


def _initialise_soup(html: str) -> Optional["BeautifulSoup"]:
    if BeautifulSoup is None:
        return None
    try:
        return BeautifulSoup(html, "lxml")  # type: ignore[call-arg]
    except Exception:  # pragma: no cover - fallback parser
        try:
            return BeautifulSoup(html, "html.parser")  # type: ignore[call-arg]
        except Exception:  # pragma: no cover - unexpected HTML edge case
            return None


def _collect_paragraphs(soup) -> list[str]:
    paragraphs: list[str] = []
    if soup is None:
        return paragraphs

    # Remove noisy sections before harvesting text.
    for tag in soup.find_all(
        ["script", "style", "noscript", "template", "header", "footer", "nav", "aside"]
    ):
        tag.decompose()

    containers = [
        element
        for element in (
            soup.find("article"),
            soup.find("main"),
            getattr(soup, "body", None),
        )
        if element
    ]
    if not containers:
        containers = [soup]

    seen_text: set[str] = set()
    for container in containers:
        for node in container.find_all(["h1", "h2", "h3", "p", "li"]):
            text = node.get_text(" ", strip=True)
            if not text:
                continue
            lowered = text.lower()
            if node.name in {"h1", "h2", "h3"}:
                candidate = f"## {text}"
            elif node.name == "li":
                candidate = f"- {text}"
            else:
                if len(text) < HEURISTIC_MIN_PARAGRAPH_CHARS:
                    continue
                if any(skip in lowered for skip in HEURISTIC_SKIP_PHRASES):
                    continue
                candidate = text
            if candidate in seen_text:
                continue
            paragraphs.append(candidate)
            seen_text.add(candidate)

    return paragraphs


def _extract_with_readability(
    url: str, html: str, source_url: Optional[str] = None
) -> dict:
    if Document is None:
        return {
            "error": "readability-lxml is not installed.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    if not html:
        return {
            "error": "No HTML provided for readability extraction.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    try:
        document = Document(html)
    except Exception as exc:  # pragma: no cover - defensive guard
        return {
            "error": f"Readability failed to parse HTML: {exc}",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    summary_html = document.summary() or ""
    soup = _initialise_soup(summary_html)
    paragraphs = _collect_paragraphs(soup)

    fallback_text = ""
    if not paragraphs and summary_html:
        stripped_html = re.sub(r"<[^>]+>", " ", summary_html)
        fallback_text = clean_text(stripped_html)

    text = clean_text("\n\n".join(paragraphs)) if paragraphs else fallback_text

    if not text:
        return {
            "error": "Readability did not yield extractable text.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    title = document.short_title() or "Untitled"

    return {
        "title": title,
        "author": "Unknown",
        "text": text,
        "source_url": source_url or url,
        "resolved_url": url,
        "parser": "readability",
    }


def _extract_with_soup_heuristic(
    url: str, html: Optional[str], source_url: Optional[str] = None
) -> dict:
    if BeautifulSoup is None:
        return {
            "error": "BeautifulSoup is not installed.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    if not html:
        return {
            "error": "No HTML provided for heuristic extraction.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    soup = _initialise_soup(html)
    if soup is None:
        return {
            "error": "Failed to parse HTML for heuristic extraction.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    paragraphs = _collect_paragraphs(soup)
    if not paragraphs:
        return {
            "error": "Heuristic extractor found no qualifying paragraphs.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    text = clean_text("\n\n".join(paragraphs))

    if len(text) < HEURISTIC_MIN_TOTAL_CHARS:
        return {
            "error": "Heuristic extractor produced insufficient text.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    title = "Untitled"
    title_node = soup.find("h1") or getattr(soup.find("title"), "string", None)
    if title_node:
        title = (
            title_node
            if isinstance(title_node, str)
            else title_node.get_text(strip=True)
        )

    meta_author = soup.find("meta", attrs={"name": "author"})
    author = meta_author.get("content", "").strip() if meta_author else "Unknown"
    if not author:
        author = "Unknown"

    return {
        "title": title or "Untitled",
        "author": author,
        "text": text,
        "source_url": source_url or url,
        "resolved_url": url,
        "parser": "soup_heuristic",
    }


def _extract_with_plaintext(
    url: str, html: Optional[str], source_url: Optional[str] = None
) -> dict:
    """Last-resort extraction that strips HTML and returns raw body text."""
    if not html:
        return {
            "error": "No HTML provided for fallback extraction.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    text_candidate = ""
    soup = _initialise_soup(html) if BeautifulSoup else None
    if soup is not None:  # type: ignore[truthy-function]
        try:
            text_candidate = soup.get_text("\n", strip=True)
        except Exception:  # pragma: no cover - defensive guard
            text_candidate = ""

    if not text_candidate:
        stripped = re.sub(r"<[^>]+>", " ", html)
        text_candidate = stripped

    cleaned = clean_text(text_candidate)
    if len(cleaned.strip()) < PLAINTEXT_MIN_TOTAL_CHARS:
        return {
            "error": "Plaintext fallback produced insufficient content.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    title = "Untitled"
    author = "Unknown"
    if soup is not None:
        title_node = soup.find("h1") or soup.find("title")
        if title_node:
            try:
                title = (
                    title_node
                    if isinstance(title_node, str)
                    else title_node.get_text(strip=True)  # type: ignore[attr-defined]
                ) or "Untitled"
            except Exception:  # pragma: no cover - defensive guard
                title = "Untitled"
        meta_author = soup.find("meta", attrs={"name": "author"})
        if meta_author and meta_author.get("content"):  # type: ignore[attr-defined]
            author = meta_author.get("content").strip() or "Unknown"  # type: ignore[attr-defined]

    return {
        "title": title,
        "author": author,
        "text": cleaned,
        "source_url": source_url or url,
        "resolved_url": url,
        "parser": "plaintext_fallback",
    }


def _extract_with_newspaper(
    url: str, html: str, source_url: Optional[str] = None
) -> dict:
    """Primary extraction method using newspaper3k."""
    if Article is None or Config is None:
        return {
            "error": "Newspaper3k is not installed.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    config = Config()
    config.browser_user_agent = USER_AGENT
    config.request_timeout = REQUEST_TIMEOUT_SECONDS
    config.memoize_articles = False
    config.fetch_images = False
    config.keep_article_html = True
    config.follow_meta_refresh = True
    config.use_meta_language = True

    article = Article(url, config=config)
    article.set_html(html)
    article.download_state = 2  # type: ignore[attr-defined]
    try:
        article.parse()
    except Exception as exc:  # pragma: no cover - defensive guard
        return {
            "error": f"Newspaper failed to parse HTML: {exc}",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    if not article.text:
        return {
            "error": "No main content could be extracted by newspaper. The page might be empty, an image, or highly dynamic.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    cleaned_text = clean_text(article.text)

    return {
        "title": article.title if article.title else "Untitled",
        "author": article.authors[0] if article.authors else "Unknown",
        "text": cleaned_text,
        "source_url": source_url or url,
        "resolved_url": url,
        "published_date": (
            article.publish_date.isoformat() if article.publish_date else None
        ),
        "image_url": article.top_image if article.top_image else None,
        "parser": "newspaper3k",
    }


def _extract_with_goose(
    url: str, html: Optional[str], source_url: Optional[str] = None
) -> dict:
    """Optional Goose3 extractor to handle stubborn layouts."""
    if Goose is None:
        return {
            "error": "Goose3 is not installed.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    config = {
        "browser_user_agent": USER_AGENT,
        "http_timeout": REQUEST_TIMEOUT_SECONDS,
        "enable_image_fetching": False,
    }

    goose = Goose(config)
    try:
        article = goose.extract(raw_html=html, url=url)
    except Exception as exc:  # pragma: no cover - defensive guard
        return {
            "error": f"Goose3 extraction failed: {exc}",
            "resolved_url": url,
            "source_url": source_url or url,
        }
    finally:
        try:
            goose.close()
        except AttributeError:
            pass

    text = getattr(article, "cleaned_text", "") or ""
    cleaned_text = clean_text(text)
    if not cleaned_text:
        return {
            "error": "Goose3 returned empty content.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    authors = getattr(article, "authors", []) or []
    if isinstance(authors, str):
        authors = [authors]

    publish_date = getattr(article, "publish_date", None)
    if hasattr(publish_date, "isoformat"):
        publish_value = publish_date.isoformat()
    else:
        publish_value = publish_date if isinstance(publish_date, str) else None

    return {
        "title": getattr(article, "title", None) or "Untitled",
        "author": authors[0] if authors else "Unknown",
        "text": cleaned_text,
        "source_url": source_url or url,
        "resolved_url": url,
        "published_date": publish_value,
        "image_url": getattr(article, "top_image", None),
        "parser": "goose3",
    }


EXTRACTOR_REGISTRY = ExtractorRegistry(
    [
        ExtractorStrategy("trafilatura", _extract_with_trafilatura),
        ExtractorStrategy("newspaper3k", _extract_with_newspaper),
        ExtractorStrategy("goose3", _extract_with_goose),
        ExtractorStrategy("readability", _extract_with_readability),
        ExtractorStrategy("soup_heuristic", _extract_with_soup_heuristic),
        ExtractorStrategy("plaintext_fallback", _extract_with_plaintext),
    ]
)


def _validate_extractors() -> None:
    for strategy in EXTRACTOR_REGISTRY.all():
        fn = strategy.extractor
        if not callable(fn):
            raise ImportError(f"Extractor {strategy.name} not callable.")
        signature = inspect.signature(fn)
        params = list(signature.parameters.values())
        if len(params) < 2:
            raise ImportError(
                f"Extractor {strategy.name} must accept at least url and html parameters."
            )
        for index, param in enumerate(params[:2]):
            if param.kind not in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                raise ImportError(
                    f"Extractor {strategy.name} parameter {index} must be positional."
                )


_validate_extractors()
