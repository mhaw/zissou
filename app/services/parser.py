import logging
import os
import re
import inspect
from collections import Counter
from typing import Callable, Optional, Tuple, Any

import trafilatura

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
    from readability import Document
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    Document = None  # type: ignore[assignment]

from trafilatura.settings import use_config  # type: ignore[import-untyped]

from app.utils.text_cleaner import clean_text

def calculate_reading_time(text: str, words_per_minute: int = 200) -> int:
    """Calculates the estimated reading time in minutes for a given text."""
    words = text.split()
    num_words = len(words)
    reading_time = num_words / words_per_minute
    return max(1, int(round(reading_time)))

from app.services.fetch import (
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
    fetch_with_resilience,
    fetch_with_playwright,
    hybrid_fetch_attempts,
    is_likely_truncated,
    recover_truncated_content,
)

logger = logging.getLogger(__name__)

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

_ENGINE_ATTEMPTS: Counter[str] = Counter()
_ENGINE_SUCCESSES: Counter[str] = Counter()
_ENGINE_FAILURES: Counter[str] = Counter()
_ENGINE_WINS: Counter[str] = Counter()

ENGINE_PIPELINE_ORDER: Tuple[str, ...] = (
    "trafilatura",
    "newspaper3k",
    "readability",
    "soup_heuristic",
    "plaintext_fallback",
)


from dataclasses import dataclass

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
            logger.warning(
                "Trafilatura metadata extraction with config failed: %s",
                exc,
            )
            metadata = None
        if metadata:
            return metadata
    try:
        return trafilatura.extract_metadata(downloaded, default_url=url)
    except Exception as exc:  # pragma: no cover - defensive for odd installs
        logger.warning("Trafilatura metadata extraction failed: %s", exc)
        return None


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

    if is_likely_truncated(parsed.get("text")):
        baseline_length = len((parsed.get("text") or "").strip())
        hybrid = _attempt_hybrid_refetch(
            resolved_url or url,
            origin_url=url,
            baseline_length=baseline_length,
        )
        if hybrid:
            return hybrid
        recovered = recover_truncated_content(
            url,
            parsed.get("text"),
            extractor=_process_html,
            fetcher=lambda target: fetch_with_resilience(target, user_agent=USER_AGENT),
        )
        if recovered:
            return recovered

    return parsed


def _process_html(
    html: str, origin_url: str, resolved_url: Optional[str] = None
) -> dict:
    target_url = resolved_url or origin_url
    errors: list[str] = []
    best_result: Optional[dict] = None
    best_length = 0
    winning_engine: Optional[str] = None

    pipeline: Tuple[Tuple[str, ExtractorFn], ...] = (
        ("trafilatura", _extract_with_trafilatura),
        ("newspaper3k", _extract_with_newspaper),
        ("readability", _extract_with_readability),
        ("soup_heuristic", _extract_with_soup_heuristic),
        ("plaintext_fallback", _extract_with_plaintext),
    )

    for engine_name, extractor in pipeline:
        _record_attempt(engine_name)
        try:
            result = extractor(target_url, html, source_url=origin_url)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning(
                "Extractor %s raised for %s: %s",
                engine_name,
                target_url,
                exc,
            )
            _record_failure(engine_name)
            errors.append(f"{engine_name}: {exc}")
            continue

        if result.get("error"):
            _record_failure(engine_name)
            errors.append(f"{engine_name}: {result['error']}")
            continue

        text = (result.get("text") or "").strip()
        text_length = len(text)

        if text_length >= ENGINE_SUCCESS_THRESHOLD:
            _record_success(engine_name)
        else:
            _record_failure(engine_name)

        if not text_length:
            continue

        if text_length > best_length:
            best_result = result
            best_length = text_length
            winning_engine = result.get("parser") or engine_name

    if best_result and winning_engine:
        metrics_winner = (
            winning_engine if best_length >= ENGINE_SUCCESS_THRESHOLD else None
        )
        if metrics_winner:
            _record_win(winning_engine)
            logger.info(
                "Extractor %s won for %s with %s characters",
                winning_engine,
                target_url,
                best_length,
            )
        else:
            logger.warning(
                "Extractor %s produced %s characters for %s (below threshold)",
                winning_engine,
                best_length,
                target_url,
            )

        best_result.setdefault("parser", winning_engine)
        best_result.setdefault("resolved_url", target_url)
        best_result.setdefault("source_url", origin_url)
        best_result["extractor_metrics"] = _build_metrics_snapshot(
            metrics_winner, winning_engine
        )
        best_result["reading_time"] = calculate_reading_time(best_result.get("text", ""))
        return best_result

    error_message = errors[0] if errors else "No extractor produced text."
    logger.error("Extraction pipeline failed for %s: %s", target_url, error_message)
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
    logger.info(
        "Attempting hybrid header retry for %s (baseline length: %s)",
        fetch_url,
        baseline_length,
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
        logger.info(
            "Hybrid retry improved extraction for %s (len %s -> %s)",
            fetch_url,
            baseline_length,
            best_length,
        )
        return best_candidate

    logger.info("Hybrid retry did not improve extraction for %s", fetch_url)
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
        for node in container.find_all("p"):
            text = node.get_text(" ", strip=True)
            if not text:
                continue
            lowered = text.lower()
            if len(text) < HEURISTIC_MIN_PARAGRAPH_CHARS:
                continue
            if any(skip in lowered for skip in HEURISTIC_SKIP_PHRASES):
                continue
            if text in seen_text:
                continue
            paragraphs.append(text)
            seen_text.add(text)

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
                    else title_node.get_text(strip=True) # type: ignore[attr-defined]
                ) or "Untitled"
            except Exception:  # pragma: no cover - defensive guard
                title = "Untitled"
        meta_author = soup.find("meta", attrs={"name": "author"})
        if meta_author and meta_author.get("content"): # type: ignore[attr-defined]
            author = meta_author.get("content").strip() or "Unknown" # type: ignore[attr-defined]

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

    article = Article(url, config=config)
    article.set_html(html)
    article.parse()

    if not article.text:
        return {
            "error": "No main content could be extracted by newspaper. The page might be empty, an image, or highly dynamic.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    return {
        "title": article.title if article.title else "Untitled",
        "author": article.authors[0] if article.authors else "Unknown",
        "text": clean_text(article.text),
        "source_url": source_url or url,
        "resolved_url": url,
        "published_date": article.publish_date.isoformat()
        if article.publish_date
        else None,
        "image_url": article.top_image if article.top_image else None,
        "parser": "newspaper3k",
    }


def _extract_with_trafilatura(
    url: str, html: Optional[str], source_url: Optional[str] = None
) -> dict:
    """Fallback extraction using trafilatura."""
    config = use_config()
    config.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
    config.set("DEFAULT", "USER_AGENT", USER_AGENT)

    if not html:
        downloaded = trafilatura.fetch_url(url, config=config)
    else:
        downloaded = html

    if not downloaded:
        return {
            "error": "Failed to fetch URL. Please check the URL and your internet connection.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    text = trafilatura.extract(
        downloaded,
        include_comments=False,
        include_tables=False,
        config=config,
        output_format="txt",
    )

    if not text:
        return {
            "error": "No main content could be extracted from the URL. The page might be empty, an image, or highly dynamic.",
            "resolved_url": url,
            "source_url": source_url or url,
        }

    text = clean_text(text)

    config.set("DEFAULT", "URL", url)
    metadata = _extract_trafilatura_metadata(downloaded, url, config)

    metadata_title = metadata.title if metadata and hasattr(metadata, "title") else None
    metadata_author = (
        metadata.author if metadata and hasattr(metadata, "author") else None
    )
    metadata_date = metadata.date if metadata and hasattr(metadata, "date") else None
    metadata_image = metadata.image if metadata and hasattr(metadata, "image") else None

    return {
        "title": metadata_title or "Untitled",
        "author": metadata_author or "Unknown",
        "text": text,
        "source_url": source_url or url,
        "resolved_url": url,
        "published_date": metadata_date,
        "image_url": metadata_image,
        "parser": "trafilatura",
    }
