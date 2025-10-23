import hashlib
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional, Any, Iterable
from urllib.parse import urlparse

from feedgen.feed import FeedGenerator  # type: ignore[import-untyped]
from lxml import etree
from flask import request
from google.api_core.exceptions import FailedPrecondition

from app.extensions import cache
from app.services.firestore_helpers import extract_index_url
from app.signals import item_updated

from .buckets import get_bucket, get_bucket_by_slug, list_buckets
from .items import list_items

logger = logging.getLogger(__name__)

DEFAULT_FEED_LANGUAGE = os.getenv("DEFAULT_FEED_LANGUAGE", "en-US")
DEFAULT_FEED_COPYRIGHT = os.getenv("DEFAULT_FEED_COPYRIGHT")
DEFAULT_FEED_MANAGING_EDITOR = os.getenv("DEFAULT_FEED_MANAGING_EDITOR")
DEFAULT_FEED_IMAGE = os.getenv("DEFAULT_FEED_IMAGE")
DEFAULT_EPISODE_IMAGE = os.getenv("DEFAULT_EPISODE_IMAGE")
FEED_ITEMS_PER_PAGE = max(1, int(os.getenv("FEED_ITEMS_PER_PAGE", "50")))
FEED_CACHE_MAX_PAGES = max(10, int(os.getenv("FEED_CACHE_MAX_PAGES", "200")))
FEED_CACHE_MAX_CONSECUTIVE_MISSES = max(
    1, int(os.getenv("FEED_CACHE_MAX_CONSECUTIVE_MISSES", "5"))
)
PUBLIC_FEED_TITLE = os.getenv("PUBLIC_FEED_TITLE", "Zissou Public Podcast")
PUBLIC_FEED_DESCRIPTION = os.getenv(
    "PUBLIC_FEED_DESCRIPTION",
    "Latest narrated stories curated by Zissou.",
)
PUBLIC_FEED_AUTHOR = os.getenv("PUBLIC_FEED_AUTHOR")
PUBLIC_FEED_OWNER_EMAIL = os.getenv(
    "PUBLIC_FEED_OWNER_EMAIL", DEFAULT_FEED_MANAGING_EDITOR or ""
)
PUBLIC_FEED_IMAGE = os.getenv("PUBLIC_FEED_IMAGE") or DEFAULT_FEED_IMAGE
PUBLIC_FEED_CATEGORIES = [
    cat.strip()
    for cat in (os.getenv("PUBLIC_FEED_ITUNES_CATEGORIES") or "").split(",")
    if cat.strip()
]
PUBLIC_FEED_ITEM_LIMIT = max(10, int(os.getenv("PUBLIC_FEED_ITEM_LIMIT", "50")))
SUBSCRIPTION_LINKS = {
    "apple": os.getenv("PUBLIC_FEED_APPLE_PODCASTS_URL"),
    "spotify": os.getenv("PUBLIC_FEED_SPOTIFY_URL"),
    "pocketcasts": os.getenv("PUBLIC_FEED_POCKETCASTS_URL"),
    "google": os.getenv("PUBLIC_FEED_GOOGLE_PODCASTS_URL"),
}


def _delete_cache_key(delete_fn: Callable[[str], bool | None], key: str) -> bool:
    """Delete a cache key and return True when a value is actually removed."""
    try:
        removed = delete_fn(key)
    except TypeError:
        # Some backends raise when the key does not exist.
        return False
    return bool(removed)


def _invalidate_cached_bucket_feed(bucket_slug: str) -> None:
    prefix = f"feed_{bucket_slug}_"
    cleared = 0
    consecutive_misses = 0

    for page in range(1, FEED_CACHE_MAX_PAGES + 1):
        cache_key = f"{prefix}{page}"
        removed = _delete_cache_key(cache.delete, cache_key)
        if removed:
            cleared += 1
            consecutive_misses = 0
            continue

        consecutive_misses += 1
        if consecutive_misses >= FEED_CACHE_MAX_CONSECUTIVE_MISSES:
            break

    # Invalidate companion link-feed cache series
    for page in range(1, FEED_CACHE_MAX_PAGES + 1):
        cache_key = f"feed_links_{bucket_slug}_{page}"
        _delete_cache_key(cache.delete, cache_key)

    logger.info(
        "feed.cache.invalidated",
        extra={
            "bucket_slug": bucket_slug,
            "removed_pages": cleared,
            "scanned_pages": min(FEED_CACHE_MAX_PAGES, cleared + consecutive_misses),
        },
    )


def _resolve_bucket_slug(identifier: str | None) -> str | None:
    if not identifier:
        return None
    bucket = get_bucket(identifier)
    if not bucket:
        bucket = get_bucket_by_slug(identifier)
    if not bucket and identifier.lower() != identifier:
        bucket = get_bucket_by_slug(identifier.lower())
    if not bucket:
        return identifier
    return bucket.slug or bucket.id


def invalidate_feed_cache(sender, **extra) -> None:
    """Signal handler to invalidate cached feeds when items change."""
    identifiers = extra.get("bucket_slugs") or extra.get("bucket_ids")
    if identifiers is None:
        identifiers = sender

    if not identifiers:
        return

    if isinstance(identifiers, (str, bytes)):
        identifiers = [identifiers]

    seen: set[str] = set()
    for identifier in identifiers:
        slug = _resolve_bucket_slug(identifier) if identifier else None
        if not slug or slug in seen:
            continue
        seen.add(slug)
        _invalidate_cached_bucket_feed(slug)


item_updated.connect(invalidate_feed_cache)


def _register_itunes_namespace():
    try:
        from feedparser.util import FeedParserDict  # type: ignore

        FeedParserDict.keymap["itunes_author"] = "author"  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - feedparser optional
        pass


def _coerce_datetime(value):
    """Coerces a value to a timezone-aware UTC datetime."""
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    to_dt = getattr(value, "to_datetime", None)
    if callable(to_dt):
        dt = to_dt()
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    logger.warning(
        f"Unable to coerce value of type {type(value)} to datetime; using now()."
    )
    return datetime.now(timezone.utc)


def _build_entry_guid(bucket_slug: str, item, published_at: datetime | None) -> str:
    """Construct a stable GUID for feed entries using the source URL and publish time when available."""
    source = (getattr(item, "sourceUrl", None) or "").strip()
    published_component = ""
    if published_at:
        published_component = _coerce_datetime(published_at).isoformat()
    if source:
        base = f"{source}|{published_component}"
    else:
        base = f"{bucket_slug}:{getattr(item, 'id', '')}:{published_component}"
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()
    return f"tag:zissou:{digest}"


def _format_duration(seconds: float) -> str:
    """Formats seconds into HH:MM:SS string for iTunes duration."""
    if seconds is None or seconds < 0:
        seconds = 0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_rfc822(dt: datetime) -> str:
    coerced = _coerce_datetime(dt)
    return coerced.strftime("%a, %d %b %Y %H:%M:%S %z")


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _guess_source_image(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.netloc:
        return f"https://www.google.com/s2/favicons/png?domain={parsed.netloc}"
    return None


SUPPORTED_PODCAST_IMAGE_EXTENSIONS = (".jpg", ".png")


def _is_supported_podcast_image_url(url: str | None) -> bool:
    if not url:
        return False
    return url.lower().endswith(SUPPORTED_PODCAST_IMAGE_EXTENSIONS)


def _choose_podcast_image_candidate(*candidates: str | None) -> str | None:
    for candidate in candidates:
        if not candidate:
            continue
        if _is_supported_podcast_image_url(candidate):
            return candidate
        logger.debug(
            "Skipping unsupported podcast image (must end with .jpg or .png): %s",
            candidate,
        )
    return None


def _select_episode_image(item, bucket) -> str | None:
    return _choose_podcast_image_candidate(
        getattr(item, "imageUrl", None),
        getattr(bucket, "rss_cover_image_url", None),
        DEFAULT_EPISODE_IMAGE,
        _guess_source_image(getattr(item, "sourceUrl", None)),
    )


def normalise_public_feed_filters(
    *, tag: str | None = None, days: str | None = None
) -> dict[str, Any]:
    """Sanitise incoming feed filters for public feeds."""
    filters: dict[str, Any] = {}
    if tag:
        clean_tag = tag.strip()
        if clean_tag:
            filters["tag"] = clean_tag
    if days:
        try:
            days_value = int(days)
            if days_value > 0:
                filters["days"] = min(days_value, 365)
        except ValueError:
            logger.debug("Ignoring invalid days filter '%s'", days)
    return filters


def _filter_items_by_days(items: Iterable[Any], days: int) -> list[Any]:
    """Restrict items to those published within the supplied day window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    filtered: list[Any] = []
    for item in items:
        published = getattr(item, "publishedAt", None) or getattr(
            item, "createdAt", None
        )
        if not published:
            continue
        published_dt = _coerce_datetime(published)
        if published_dt >= cutoff:
            filtered.append(item)
    return filtered


def get_public_feed_items(
    filters: dict[str, Any] | None = None,
    *,
    limit: int = PUBLIC_FEED_ITEM_LIMIT,
) -> list[Any]:
    """Fetch public items eligible for the aggregated feed."""
    filters = filters or {}
    tags_filter = None
    if tag := filters.get("tag"):
        tags_filter = [tag]

    try:
        items, _ = list_items(
            user_id=None,
            bucket_slug=None,
            tags=tags_filter,
            limit=limit,
            include_archived=False,
            include_read=True,
            sort_by="newest",
        )
    except FailedPrecondition as exc:
        _handle_missing_index(exc)

    items = [item for item in items if getattr(item, "audioUrl", None)]

    days = filters.get("days")
    if days:
        items = _filter_items_by_days(items, days)

    # Ensure newest-first order and trimmed to limit
    items.sort(
        key=lambda i: _coerce_datetime(
            getattr(i, "publishedAt", None) or getattr(i, "createdAt", None)
        ),
        reverse=True,
    )
    return items[:limit]


def get_public_feed_metadata(
    *,
    filters: dict[str, Any] | None = None,
    page_url: str,
    rss_url: str,
) -> dict[str, Any]:
    """Build presentation metadata for public feed landing pages."""
    filters = filters or {}
    tag = filters.get("tag")
    title = PUBLIC_FEED_TITLE
    description = PUBLIC_FEED_DESCRIPTION
    if tag:
        title = f"{PUBLIC_FEED_TITLE} · {tag}"
        description = f"Latest Zissou episodes tagged '{tag}'."
    image_url = PUBLIC_FEED_IMAGE or DEFAULT_FEED_IMAGE or _guess_source_image(page_url)

    return {
        "title": title,
        "description": description,
        "image_url": image_url,
        "page_url": page_url,
        "rss_url": rss_url,
        "tag": tag,
    }


def get_public_feed_subscription_links(rss_url: str) -> dict[str, Optional[str]]:
    """Expose configured subscription endpoints alongside the raw RSS URL."""
    links = {
        "rss": rss_url,
    }
    for key, url in SUBSCRIPTION_LINKS.items():
        if url:
            links[key] = url
    return links


def build_public_feed_schema(metadata: dict[str, Any], items: list[Any]) -> str:
    """Return PodcastSeries JSON-LD for landing pages."""
    episodes = []
    for item in items[:10]:
        audio_url = getattr(item, "audioUrl", None)
        if not audio_url:
            continue
        published = getattr(item, "publishedAt", None) or getattr(
            item, "createdAt", None
        )
        published_iso = _coerce_datetime(published).isoformat()
        duration_seconds = getattr(item, "durationSeconds", None) or 0
        episode = {
            "@type": "PodcastEpisode",
            "name": getattr(item, "title", "Untitled Episode"),
            "description": (_clean_text(getattr(item, "summary_text", None)) or ""),
            "datePublished": published_iso,
            "timeRequired": f"PT{int(duration_seconds)}S",
            "url": getattr(item, "sourceUrl", metadata.get("page_url")),
            "audio": {
                "@type": "AudioObject",
                "contentUrl": audio_url,
                "encodingFormat": getattr(item, "audioMimeType", "audio/mpeg"),
            },
        }
        episodes.append(episode)

    schema = {
        "@context": "https://schema.org",
        "@type": "PodcastSeries",
        "name": metadata.get("title"),
        "description": metadata.get("description"),
        "url": metadata.get("page_url"),
        "image": metadata.get("image_url"),
        "webFeed": metadata.get("rss_url"),
        "author": metadata.get("title"),
        "episode": episodes,
    }
    return json.dumps(schema, ensure_ascii=False)


class FeedGenerationError(Exception):
    pass


class FeedIndexBuildingError(FeedGenerationError):
    """Raised when Firestore requires a composite index that is still building."""

    def __init__(self, hint: str | None = None):
        super().__init__("Firestore index is building")
        self.hint = hint


def _handle_missing_index(exc: FailedPrecondition) -> None:
    hint = extract_index_url(exc)
    try:
        url = request.path  # Access within request context when available.
    except RuntimeError:
        url = None
    logger.warning(
        "firestore.index.missing",
        extra={"url": url, "hint": hint},
    )
    raise FeedIndexBuildingError(hint=hint) from exc


@cache.cached(timeout=300, key_prefix="public_feed")
def build_public_feed_xml(
    *,
    items: list[Any],
    feed_url: str,
    filters: dict[str, Any] | None = None,
) -> bytes:
    """Generate the public RSS XML payload using existing feed helpers."""
    start_time = time.time()
    try:
        return _build_public_feed_xml(items=items, feed_url=feed_url, filters=filters)
    except FeedIndexBuildingError:
        raise
    except Exception as e:
        logger.exception("Error generating public feed")
        cached_feed = cache.get(f"public_feed_{filters}")
        if cached_feed:
            logger.warning("Serving stale public feed")
            return cached_feed
        raise e
    finally:
        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(
            "feed.generation.complete",
            extra={"elapsed_ms": elapsed_ms, "feed_type": "public", "filters": filters},
        )


def _build_public_feed_xml(
    *,
    items: list[Any],
    feed_url: str,
    filters: dict[str, Any] | None = None,
) -> bytes:
    filters = filters or {}
    fg = FeedGenerator()
    fg.load_extension("podcast")
    _register_itunes_namespace()

    app_url = os.getenv("APP_URL", "http://localhost:8080").rstrip("/")
    landing_path = "/feeds/public"
    if tag := filters.get("tag"):
        landing_path = f"/feeds/tag/{tag}"
    feed_landing_url = f"{app_url}{landing_path}"

    description = PUBLIC_FEED_DESCRIPTION
    if filters.get("tag"):
        description = f"Latest Zissou episodes tagged '{filters['tag']}'."

    fg.title(
        PUBLIC_FEED_TITLE
        if not filters.get("tag")
        else f"{PUBLIC_FEED_TITLE} · {filters['tag']}"
    )
    fg.description(description)
    fg.subtitle(_truncate(description, 255))
    fg.link(href=feed_landing_url, rel="alternate")
    fg.language(DEFAULT_FEED_LANGUAGE)

    author = PUBLIC_FEED_AUTHOR or DEFAULT_FEED_MANAGING_EDITOR
    if author:
        fg.podcast.itunes_author(author)

    owner_email = PUBLIC_FEED_OWNER_EMAIL or DEFAULT_FEED_MANAGING_EDITOR
    if owner_email or author:
        fg.podcast.itunes_owner(
            name=author or PUBLIC_FEED_TITLE, email=owner_email or ""
        )

    copyright_holder = DEFAULT_FEED_COPYRIGHT or (
        author and f"© {datetime.now().year} {author}"
    )
    if copyright_holder:
        fg.copyright(copyright_holder)

    summary = _truncate(description, 400)
    fg.podcast.itunes_summary(summary)
    fg.podcast.itunes_subtitle(_truncate(description, 120))

    feed_image = _choose_podcast_image_candidate(
        PUBLIC_FEED_IMAGE,
        DEFAULT_FEED_IMAGE,
        _guess_source_image(feed_landing_url),
    )
    if feed_image:
        fg.podcast.itunes_image(feed_image)
        fg.image(feed_image)

    categories = PUBLIC_FEED_CATEGORIES or ["Technology"]
    for category in categories:
        fg.podcast.itunes_category(category)

    fg.link(href=feed_url, rel="self")

    bucket_lookup = {b.id: b for b in list_buckets() if getattr(b, "id", None)}
    feed_keywords: set[str] = set()
    entry_keywords_map: list[list[str]] = []
    feed_last_updated: datetime | None = None

    for item in items:
        try:
            fe = fg.add_entry()
            pub_date = getattr(item, "publishedAt", None) or getattr(
                item, "createdAt", None
            )
            guid = _build_entry_guid("public", item, pub_date)
            fe.id(guid)
            fe.guid(guid, permalink=False)
            fe.title(getattr(item, "title", "Untitled Episode"))

            if getattr(item, "sourceUrl", None):
                fe.link(href=item.sourceUrl)

            summary_text = (
                _clean_text(getattr(item, "summary_text", None))
                or _clean_text(getattr(item, "text", ""))
                or _clean_text(getattr(item, "title", ""))
                or "No summary available."
            )
            summary_text = _truncate(summary_text, 400)
            fe.description(summary_text)
            fe.summary(summary_text)
            fe.podcast.itunes_summary(summary_text)
            fe.podcast.itunes_subtitle(_truncate(summary_text, 120))

            if author:
                fe.podcast.itunes_author(author)

            bucket_stub = None
            if getattr(item, "buckets", None):
                for bucket_id in item.buckets or []:
                    candidate = bucket_lookup.get(bucket_id)
                    if candidate and candidate.name:
                        fe.category(term=candidate.name)
                        if not bucket_stub:
                            bucket_stub = candidate

            episode_image = (
                _select_episode_image(item, bucket_stub)
                if bucket_stub
                else _choose_podcast_image_candidate(
                    getattr(item, "imageUrl", None),
                    feed_image,
                    DEFAULT_EPISODE_IMAGE,
                )
            )
            if episode_image:
                fe.podcast.itunes_image(episode_image)

            if getattr(item, "audioUrl", None):
                mime_type = getattr(item, "audioMimeType", None) or "audio/mpeg"
                fe.enclosure(
                    url=item.audioUrl,
                    length=str(getattr(item, "audioSizeBytes", None) or 0),
                    type=mime_type,
                )
            fe.podcast.itunes_duration(
                _format_duration(getattr(item, "durationSeconds", None) or 0)
            )

            pub_dt = (
                pub_date
                or getattr(item, "createdAt", None)
                or datetime.now(timezone.utc)
            )
            fe.pubDate(_format_rfc822(pub_dt))
            fe.updated(_coerce_datetime(getattr(item, "updatedAt", None) or pub_dt))

            item_tags = getattr(item, "tags", None) or []
            for tag_value in item_tags:
                fe.category(term=tag_value)

            keywords = set(item_tags)
            if bucket_stub and getattr(bucket_stub, "name", None):
                keywords.add(bucket_stub.name)
            keywords = sorted(keywords)
            entry_keywords_map.append(keywords)
            if keywords:
                feed_keywords.update(keywords)

            current_updated = _coerce_datetime(
                getattr(item, "updatedAt", None) or pub_dt
            )
            feed_last_updated = (
                current_updated
                if feed_last_updated is None
                else max(feed_last_updated, current_updated)
            )
        except Exception:  # pragma: no cover - resilience during feed build
            logger.exception(
                "Error processing public feed item %s",
                getattr(item, "id", "unknown"),
                exc_info=True,
            )
            continue

    try:
        fg.lastBuildDate(feed_last_updated or datetime.now(timezone.utc))
        rss_bytes = fg.rss_str(pretty=True)

        if feed_keywords or any(entry_keywords_map):
            itunes_ns = "http://www.itunes.com/dtds/podcast-1.0.dtd"
            root = etree.fromstring(rss_bytes)
            channel = root.find("channel")
            if channel is not None:
                if feed_keywords:
                    for existing in channel.findall(f"{{{itunes_ns}}}keywords"):
                        channel.remove(existing)
                    kw_elem = etree.SubElement(channel, f"{{{itunes_ns}}}keywords")
                    kw_elem.text = ", ".join(sorted(feed_keywords))
                item_elems = channel.findall("item")
                for elem, keywords in zip(item_elems, entry_keywords_map):
                    if keywords:
                        for existing in elem.findall(f"{{{itunes_ns}}}keywords"):
                            elem.remove(existing)
                        kw_elem = etree.SubElement(elem, f"{{{itunes_ns}}}keywords")
                        kw_elem.text = ", ".join(keywords)
            rss_bytes = etree.tostring(
                root, encoding="utf-8", xml_declaration=True, pretty_print=True
            )
    except Exception:  # pragma: no cover - defensive handling
        logger.exception("Error finalising public RSS feed", exc_info=True)
        raise

    return rss_bytes


def generate_feed_for_bucket(
    bucket_slug: str,
    feed_base_url: str,
    page: int = 1,
    *,
    require_audio: bool = False,
) -> bytes:
    """Generate a paginated RSS feed for the provided bucket slug."""
    try:
        return _generate_feed_for_bucket(
            bucket_slug, feed_base_url, page, require_audio=require_audio
        )
    except FeedIndexBuildingError:
        raise
    except Exception as e:
        logger.exception(f"Error generating feed for bucket {bucket_slug}")
        cached_feed = cache.get(f"feed_{bucket_slug}_{page}")
        if cached_feed:
            logger.warning(f"Serving stale feed for bucket {bucket_slug}")
            return cached_feed
        raise e


def _generate_feed_for_bucket(
    bucket_slug: str,
    feed_base_url: str,
    page: int = 1,
    *,
    require_audio: bool = False,
) -> bytes:
    start_time = time.time()
    emitted_count = 0
    has_next = False
    initial_items_count = 0
    try:
        if page < 1:
            raise FeedGenerationError("Page number must be greater than or equal to 1.")

        try:
            bucket = get_bucket_by_slug(bucket_slug)
        except Exception as exc:  # pragma: no cover - Firestore client errors
            logger.exception("Error fetching bucket %s", bucket_slug, exc_info=True)
            raise FeedGenerationError(
                f"Failed to retrieve bucket {bucket_slug}."
            ) from exc

        if not bucket:
            logger.warning("Bucket with slug '%s' not found.", bucket_slug)
            raise FeedGenerationError(f"Bucket with slug '{bucket_slug}' not found.")

        list_items_fn = getattr(list_items, "__wrapped__", list_items)
        cursor: Optional[str] = None
        bucket_filter_value = bucket.id or bucket.slug
        if not bucket_filter_value:
            logger.warning(
                "Bucket %s is missing both id and slug; feed generation aborted.",
                bucket_slug,
            )
            raise FeedGenerationError(
                f"Bucket '{bucket_slug}' is missing identifier metadata."
            )

        if page > 1:
            for _ in range(1, page):
                _, cursor = list_items_fn(
                    user_id=None,
                    bucket_slug=bucket_filter_value,
                    limit=FEED_ITEMS_PER_PAGE,
                    cursor=cursor,
                    include_archived=False,
                    include_read=True,
                )
                if not cursor:
                    raise FeedGenerationError("Requested feed page is out of range.")

        try:
            items, next_cursor = list_items_fn(
                user_id=None,
                bucket_slug=bucket_filter_value,
                limit=FEED_ITEMS_PER_PAGE,
                cursor=cursor,
                include_archived=False,
                include_read=True,
            )
        except FailedPrecondition as exc:
            _handle_missing_index(exc)

        has_next = bool(next_cursor)
        initial_items_count = len(items)

        fg = FeedGenerator()
        fg.load_extension("podcast")
        _register_itunes_namespace()

        app_url = os.getenv("APP_URL", "http://localhost:8080")
        feed_description = bucket.description or bucket.name or ""
        cleaned_feed_desc = _clean_text(feed_description)

        fg.title(bucket.name)
        fg.description(feed_description)
        fg.subtitle(cleaned_feed_desc)
        fg.link(href=f"{app_url}/buckets/{bucket.id}", rel="alternate")
        fg.language(DEFAULT_FEED_LANGUAGE)

        managing_editor = bucket.rss_owner_email or DEFAULT_FEED_MANAGING_EDITOR
        if managing_editor:
            fg.managingEditor(managing_editor)

        copyright_holder = DEFAULT_FEED_COPYRIGHT or (
            bucket.rss_author_name
            and f"© {datetime.now().year} {bucket.rss_author_name}"
        )
        if copyright_holder:
            fg.copyright(copyright_holder)

        fg.podcast.itunes_author(bucket.rss_author_name)
        fg.podcast.itunes_owner(
            name=bucket.rss_author_name, email=bucket.rss_owner_email
        )
        fg.podcast.itunes_subtitle(_truncate(cleaned_feed_desc or bucket.name, 255))
        fg.podcast.itunes_summary(_truncate(cleaned_feed_desc or bucket.name, 400))

        bucket_feed_image = _choose_podcast_image_candidate(
            getattr(bucket, "rss_cover_image_url", None),
            DEFAULT_FEED_IMAGE,
            _guess_source_image(app_url),
        )
        if bucket_feed_image:
            fg.podcast.itunes_image(bucket_feed_image)
            fg.image(bucket_feed_image)
        else:
            logger.debug(
                "No valid podcast cover image found for bucket %s", bucket.slug
            )

        itunes_categories = bucket.itunes_categories or []
        if not itunes_categories:
            fg.podcast.itunes_category("Technology")
        else:
            for category in itunes_categories:
                fg.podcast.itunes_category(category)

        fg.link(href=f"{feed_base_url}?page={page}", rel="self")
        if page > 1:
            fg.link(href=f"{feed_base_url}?page=1", rel="first")
            fg.link(href=f"{feed_base_url}?page={page - 1}", rel="previous")
        if has_next:
            fg.link(href=f"{feed_base_url}?page={page + 1}", rel="next")

        bucket_lookup = {b.id: b for b in list_buckets() if getattr(b, "id", None)}
        feed_keywords: set[str] = set()
        feed_last_updated: datetime | None = None
        entry_keywords_map: list[list[str]] = []

        for item in items:
            try:
                if require_audio and not getattr(item, "audioUrl", None):
                    continue
                fe = fg.add_entry()
                pub_date = item.publishedAt or item.createdAt
                guid = _build_entry_guid(bucket_slug, item, pub_date)
                fe.id(guid)
                fe.guid(guid, permalink=False)
                fe.title(item.title)
                if item.sourceUrl:
                    fe.link(href=item.sourceUrl)

                summary_text = (
                    _clean_text(getattr(item, "text", ""))
                    or _clean_text(item.title)
                    or "No summary available."
                )
                summary_text = _truncate(summary_text, 400)
                fe.description(summary_text)
                fe.summary(summary_text)
                fe.podcast.itunes_summary(summary_text)
                fe.podcast.itunes_subtitle(_truncate(summary_text, 120))
                fe.podcast.itunes_author(
                    getattr(item, "author", None) or bucket.rss_author_name
                )

                episode_image = _select_episode_image(item, bucket)
                if episode_image:
                    fe.podcast.itunes_image(episode_image)

                if item.audioUrl:
                    mime_type = getattr(item, "audioMimeType", None) or "audio/mpeg"
                    fe.enclosure(
                        url=item.audioUrl,
                        length=str(item.audioSizeBytes or 0),
                        type=mime_type,
                    )
                fe.podcast.itunes_duration(_format_duration(item.durationSeconds or 0))

                pub_dt = pub_date or item.createdAt
                fe.pubDate(_format_rfc822(pub_dt))
                fe.updated(_coerce_datetime(item.updatedAt or pub_dt))

                item_tags = getattr(item, "tags", []) or []
                item_bucket_names: list[str] = []
                for bucket_id in getattr(item, "buckets", []) or []:
                    bucket_obj = bucket_lookup.get(bucket_id)
                    if bucket_obj and bucket_obj.name:
                        item_bucket_names.append(bucket_obj.name)
                        fe.category(term=bucket_obj.name)
                for tag in item_tags:
                    fe.category(term=tag)

                keywords = sorted({*item_tags, *item_bucket_names})
                entry_keywords_map.append(keywords)
                if keywords:
                    feed_keywords.update(keywords)

                current_updated = _coerce_datetime(item.updatedAt or pub_dt)
                feed_last_updated = (
                    current_updated
                    if feed_last_updated is None
                    else max(feed_last_updated, current_updated)
                )
                emitted_count += 1
            except Exception:  # pragma: no cover - resilience during feed build
                logger.exception(
                    "Error processing item %s for feed",
                    getattr(item, "id", "unknown"),
                    exc_info=True,
                )
                continue

        try:
            fg.lastBuildDate(feed_last_updated or datetime.now(timezone.utc))

            rss_bytes = fg.rss_str(pretty=True)
            if feed_keywords or any(entry_keywords_map):
                itunes_ns = "http://www.itunes.com/dtds/podcast-1.0.dtd"
                root = etree.fromstring(rss_bytes)
                channel = root.find("channel")
                if channel is not None:
                    if feed_keywords:
                        for existing in channel.findall(f"{{{itunes_ns}}}keywords"):
                            channel.remove(existing)
                        kw_elem = etree.SubElement(channel, f"{{{itunes_ns}}}keywords")
                        kw_elem.text = ", ".join(sorted(feed_keywords))
                    item_elems = channel.findall("item")
                    for elem, keywords in zip(item_elems, entry_keywords_map):
                        if keywords:
                            for existing in elem.findall(f"{{{itunes_ns}}}keywords"):
                                elem.remove(existing)
                            kw_elem = etree.SubElement(elem, f"{{{itunes_ns}}}keywords")
                            kw_elem.text = ", ".join(keywords)
                rss_bytes = etree.tostring(
                    root, encoding="utf-8", xml_declaration=True, pretty_print=True
                )
        except Exception:  # pragma: no cover - defensive handling
            logger.exception(
                "Error finalizing RSS feed for bucket %s", bucket_slug, exc_info=True
            )
            raise

        return rss_bytes
    finally:
        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(
            "feed.generation.complete",
            extra={
                "elapsed_ms": elapsed_ms,
                "feed_type": "bucket",
                "bucket_slug": bucket_slug,
                "page": page,
                "items_initial": initial_items_count,
                "items_emitted": emitted_count,
                "has_next": has_next,
                "require_audio": require_audio,
            },
        )
