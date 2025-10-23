from __future__ import annotations

import json
import os
import re
import textwrap
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import structlog

from app.services import items as items_service
from app.utils.correlation import ensure_correlation_id

logger = structlog.get_logger(__name__)

_MAX_WORKERS = max(1, min(int(os.getenv("AI_ENRICHMENT_MAX_WORKERS", "2")), 8))
_MAX_CHARS = max(2000, int(os.getenv("AI_ENRICHMENT_MAX_CHARS", "12000")))
_SUMMARY_WORD_LIMIT = max(50, int(os.getenv("SUMMARY_MAX_WORDS", "300")))
_TAG_LIMIT = max(3, min(int(os.getenv("AUTO_TAG_LIMIT", "6")), 10))

_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS)

_STOP_WORDS = {
    "the",
    "and",
    "for",
    "that",
    "with",
    "from",
    "this",
    "have",
    "about",
    "their",
    "which",
    "would",
    "there",
    "could",
    "should",
    "because",
    "into",
    "where",
    "while",
    "after",
    "before",
    "between",
    "during",
    "these",
    "those",
    "over",
    "under",
    "through",
    "being",
    "also",
    "many",
    "such",
    "when",
    "were",
    "they",
    "them",
    "said",
    "been",
    "like",
    "just",
    "will",
}


def _is_summary_enabled() -> bool:
    return os.getenv("ENABLE_SUMMARY", "false").lower() in {"true", "1", "yes"}


def _is_auto_tag_enabled() -> bool:
    return os.getenv("ENABLE_AUTO_TAGS", "false").lower() in {"true", "1", "yes"}


def maybe_schedule_enrichment(
    item_id: str, article_text: str, correlation_id: Optional[str]
) -> None:
    """Schedule AI enrichment if requested and not already present."""
    if not _is_summary_enabled() and not _is_auto_tag_enabled():
        return

    text = _clip_text(article_text)
    if not text:
        return

    # Avoid rescheduling if the item already has enrichment.
    existing_item = items_service.get_item(item_id)
    if existing_item and existing_item.summary_text and existing_item.auto_tags:
        return

    _executor.submit(_enrich_item, item_id, text, correlation_id)


def _enrich_item(item_id: str, text: str, correlation_id: Optional[str]) -> None:
    ensure_correlation_id(correlation_id)
    try:
        summary, tags = generate_enrichment(text)
    except Exception as exc:  # pragma: no cover - defensive guard
        # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
        logger.error(
            event="ai_enrichment_failed",
            operation="ai.enrichment_failed",
            item_id=item_id,
            error=str(exc),
        )
        return

    if summary and _is_summary_enabled():
        try:
            items_service.update_item_summary(item_id, summary)
        except Exception as exc:  # pragma: no cover - defensive guard
            # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
            logger.error(
                event="ai_summary_persist_failed",
                operation="ai.summary_persist_failed",
                item_id=item_id,
                error=str(exc),
            )
    if tags and _is_auto_tag_enabled():
        try:
            items_service.update_item_auto_tags(item_id, tags)
        except Exception as exc:  # pragma: no cover - defensive guard
            # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
            logger.error(
                event="ai_auto_tags_persist_failed",
                operation="ai.auto_tags_persist_failed",
                item_id=item_id,
                error=str(exc),
            )


def generate_enrichment(text: str) -> tuple[Optional[str], list[str]]:
    """Generate summary and tags using the configured provider with heuristics fallback."""
    provider = (
        os.getenv("SUMMARY_PROVIDER") or os.getenv("AUTO_TAG_PROVIDER") or ""
    ).lower()
    clipped = _clip_text(text)

    if provider == "gemini":
        result = _query_gemini(clipped)
        if result:
            return result
    elif provider == "openai":
        result = _query_openai(clipped)
        if result:
            return result

    return _fallback_summary(clipped), _fallback_tags(clipped)


def _clip_text(text: str) -> str:
    if not text:
        return ""
    stripped = text.strip()
    if len(stripped) <= _MAX_CHARS:
        return stripped
    return stripped[:_MAX_CHARS]


def _query_gemini(text: str) -> Optional[tuple[str, list[str]]]:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GENERATIVEAI_API_KEY")
    if not api_key:
        # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
        logger.debug(
            event="ai_provider_skipped",
            operation="ai.provider_skipped",
            provider="gemini",
            reason="missing_api_key",
        )
        return None
    try:
        import google.generativeai as genai  # type: ignore[import-untyped]
    except ImportError:
        # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
        logger.warning(
            event="ai_provider_unavailable",
            operation="ai.provider_unavailable",
            provider="gemini",
            reason="package_missing",
        )
        return None

    model_name = os.getenv("SUMMARY_MODEL", "gemini-1.5-flash")
    prompt = textwrap.dedent(
        f"""
        Summarise the following article in fewer than {_SUMMARY_WORD_LIMIT} words
        and extract up to {_TAG_LIMIT} concise topical tags (each 1-3 words).

        Return a JSON object with this schema:
        {{
          "summary": "string",
          "tags": ["tag one", "tag two"]
        }}

        Article:
        {text}
        """
    ).strip()
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        result = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
        )
        raw = getattr(result, "text", None) or getattr(result, "candidates", [])
        payload = _parse_structured_response(raw)
        if payload:
            return payload
    except Exception as exc:  # pragma: no cover - external dependency
        # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
        logger.error(
            event="ai_provider_error",
            operation="ai.provider_error",
            provider="gemini",
            error=str(exc),
        )
    return None


def _query_openai(text: str) -> Optional[tuple[str, list[str]]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI  # type: ignore[import-untyped]
    except ImportError:
        # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
        logger.warning(
            event="ai_provider_unavailable",
            operation="ai.provider_unavailable",
            provider="openai",
            reason="package_missing",
        )
        return None

    model = os.getenv("SUMMARY_MODEL", "gpt-4o-mini")
    prompt = textwrap.dedent(
        f"""
        Summarise the following article in fewer than {_SUMMARY_WORD_LIMIT} words
        and extract up to {_TAG_LIMIT} concise topical tags (each 1-3 words).
        Respond using JSON with keys "summary" (string) and "tags" (array of strings).

        Article:
        {text}
        """
    ).strip()
    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=model,
            input=prompt,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = getattr(response, "output_text", None) or getattr(
            response, "choices", None
        )
        payload = _parse_structured_response(raw)
        if payload:
            return payload
    except Exception as exc:  # pragma: no cover - external dependency
        # structlog uses the positional argument for the event name; keep ``event`` keyword-only.
        logger.error(
            event="ai_provider_error",
            operation="ai.provider_error",
            provider="openai",
            error=str(exc),
        )
    return None


def _parse_structured_response(raw) -> Optional[tuple[str, list[str]]]:
    if raw is None:
        return None
    if isinstance(raw, list):
        # Try to find a text attribute within candidate list
        for candidate in raw:
            text = getattr(candidate, "text", None)
            if text:
                raw = text
                break
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    summary = payload.get("summary")
    tags = payload.get("tags")
    summary_text = (
        _truncate_words(summary or "", _SUMMARY_WORD_LIMIT) if summary else None
    )
    tag_list = _normalize_tags(tags)
    return summary_text, tag_list


def _normalize_tags(tags) -> list[str]:
    if not isinstance(tags, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw_tag in tags:
        if not isinstance(raw_tag, str):
            continue
        tag = raw_tag.strip()
        if not tag:
            continue
        lowered = tag.lower()
        if lowered in seen:
            continue
        cleaned.append(tag)
        seen.add(lowered)
        if len(cleaned) >= _TAG_LIMIT:
            break
    return cleaned


def _truncate_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    return " ".join(words[:limit]).strip() + "…"


def _fallback_summary(text: str) -> str:
    if not text:
        return ""
    shortened = textwrap.shorten(text, width=2000, placeholder="")
    return _truncate_words(shortened, _SUMMARY_WORD_LIMIT)


def _fallback_tags(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z\\-]{2,}", text.lower())
    frequency: dict[str, int] = {}
    for word in words:
        if word in _STOP_WORDS:
            continue
        frequency[word] = frequency.get(word, 0) + 1

    if not frequency:
        return []

    sorted_words = sorted(
        frequency.items(),
        key=lambda item: (-item[1], item[0]),
    )
    tags: list[str] = []
    for word, _ in sorted_words:
        tags.append(word.title())
        if len(tags) >= _TAG_LIMIT:
            break
    return tags
