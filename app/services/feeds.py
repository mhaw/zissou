import hashlib
import html
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from feedgen.feed import FeedGenerator  # type: ignore[import-untyped]
from lxml import etree

from .buckets import get_bucket_by_slug, list_buckets
from .items import list_items  # Use the existing service to fetch items

logger = logging.getLogger(__name__)

DEFAULT_FEED_LANGUAGE = os.getenv("DEFAULT_FEED_LANGUAGE", "en-US")
DEFAULT_FEED_COPYRIGHT = os.getenv("DEFAULT_FEED_COPYRIGHT")
DEFAULT_FEED_MANAGING_EDITOR = os.getenv("DEFAULT_FEED_MANAGING_EDITOR")
DEFAULT_FEED_IMAGE = os.getenv("DEFAULT_FEED_IMAGE")
DEFAULT_EPISODE_IMAGE = os.getenv("DEFAULT_EPISODE_IMAGE")


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


def generate_feed_for_bucket(
    bucket_slug: str, feed_base_url: str, page: int = 1
) -> str:
    """Generates a paginated RSS feed for a given bucket slug."""
    bucket = get_bucket_by_slug(bucket_slug)
    if not bucket:
        raise ValueError(f"Bucket with slug '{bucket_slug}' not found.")

    list_items_fn = getattr(list_items, "__wrapped__", list_items)
    all_items, _ = list_items_fn(
        user_id="dummy_user_id", bucket_slug=bucket.slug, limit=200
    )

    per_page = 50
    start_index = (page - 1) * per_page
    end_index = start_index + per_page
    items = all_items[start_index:end_index]

    total_pages = (len(all_items) + per_page - 1) // per_page
    has_next = page < total_pages

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
        bucket.rss_author_name and f"© {datetime.now().year} {bucket.rss_author_name}"
    )
    if copyright_holder:
        fg.copyright(copyright_holder)

    fg.podcast.itunes_author(bucket.rss_author_name)
    fg.podcast.itunes_owner(name=bucket.rss_author_name, email=bucket.rss_owner_email)
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
        logger.debug("No valid podcast cover image found for bucket %s", bucket.slug)

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
    if total_pages > 0:
        fg.link(href=f"{feed_base_url}?page={total_pages}", rel="last")

    bucket_lookup = {b.id: b for b in list_buckets() if getattr(b, "id", None)}
    feed_keywords: set[str] = set()
    feed_last_updated: datetime | None = None
    entry_keywords_map: list[list[str]] = []

    for item in items:
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

    return rss_bytes
