import pytest
from datetime import datetime, timezone, timedelta
import hashlib
from unittest.mock import patch
import feedparser
from xml.etree import ElementTree as ET
from types import SimpleNamespace
from google.api_core.exceptions import FailedPrecondition

from app.services.feeds import (
    FeedIndexBuildingError,
    _coerce_datetime,
    generate_feed_for_bucket,
    _filter_items_by_days,
    get_public_feed_items,
    normalise_public_feed_filters,
)
import app.services.feeds as feeds
from app.models.item import Item
from app.models.bucket import Bucket
from tools.validate_feed import validate_feed


# Mock Firestore's DatetimeWithNanoseconds for testing the coercion helper
class MockDatetimeWithNanoseconds:
    def __init__(self, dt):
        self._dt = dt

    def to_datetime(self):
        return self._dt


@pytest.mark.parametrize(
    "input_val, expected_type",
    [
        (datetime(2023, 1, 1, 12, 0, 0), datetime),
        (datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc), datetime),
        (
            MockDatetimeWithNanoseconds(
                datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
            ),
            datetime,
        ),
        (None, datetime),
    ],
)
def test_coerce_datetime(input_val, expected_type):
    """Tests that the datetime coercion function handles various inputs correctly."""
    coerced = _coerce_datetime(input_val)
    assert isinstance(coerced, expected_type)
    assert coerced.tzinfo is not None, "Coerced datetime must be timezone-aware"


@patch("app.services.feeds.get_bucket_by_slug")
@patch("app.services.feeds.list_items")
@patch("app.services.feeds.list_buckets")
def test_generate_feed_for_bucket(
    mock_list_buckets, mock_list_items, mock_get_bucket_by_slug, tmp_path
):
    """Tests the full feed generation logic, validating the output with feedparser."""
    # 1. Setup Mock Data
    mock_bucket = Bucket(
        id="bucket123",
        name="Test Bucket",
        slug="test-bucket",
        description="A bucket for testing.",
        rss_author_name="Tester",
        rss_owner_email="test@example.com",
        rss_cover_image_url="http://example.com/cover.png",
        itunes_categories=["Technology"],
    )
    mock_get_bucket_by_slug.return_value = mock_bucket
    mock_list_buckets.return_value = [mock_bucket]

    mock_items = [
        Item(
            id="item123",
            title="Test Item 1",
            sourceUrl="http://example.com/article1",
            text="This is a test.",
            audioUrl="https://storage.googleapis.com/test-bucket/audio1.mp3",
            audioSizeBytes=123456,
            durationSeconds=123.0,
            imageUrl="http://example.com/image1.png",
            tags=["news", "tech"],
            buckets=["bucket123"],
            publishedAt=datetime(2023, 9, 8, 12, 0, 0, tzinfo=timezone.utc),
            createdAt=datetime(2023, 9, 8, 12, 0, 0, tzinfo=timezone.utc),
            updatedAt=datetime(2023, 9, 8, 12, 0, 0, tzinfo=timezone.utc),
        )
    ]
    mock_list_items.return_value = (mock_items, None)

    # 2. Generate Feed
    feed_xml = generate_feed_for_bucket(
        "test-bucket", "https://example.com/feeds/test-bucket.xml"
    )
    assert feed_xml is not None
    assert b"<rss" in feed_xml

    feed_path = tmp_path / "test-feed.xml"
    feed_path.write_bytes(feed_xml)
    validate_feed(feed_path)

    # 3. Validate with feedparser
    d = feedparser.parse(feed_xml)
    assert d.bozo == 0, f"Feed is not well-formed: {d.bozo_exception}"

    root = ET.fromstring(feed_xml)
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    channel_elem = root.find("channel")
    assert channel_elem is not None

    # Channel checks
    assert d.feed.title == "Test Bucket"
    assert d.feed.itunes_author == "Tester"
    assert d.feed.language.lower() == "en-us"
    assert d.feed.publisher_detail["email"] == "test@example.com"
    assert channel_elem.findtext("managingEditor") == "test@example.com"
    assert (
        channel_elem.findtext("itunes:subtitle", default="", namespaces=ns)
        == "A bucket for testing."
    )
    assert (
        channel_elem.findtext("itunes:summary", default="", namespaces=ns)
        == "A bucket for testing."
    )
    assert (
        channel_elem.findtext("itunes:keywords", default="", namespaces=ns)
        == "Test Bucket, news, tech"
    )

    # Entry checks
    assert len(d.entries) == 1
    entry = d.entries[0]
    item_elem = channel_elem.find("item")
    assert item_elem is not None
    assert entry.title == "Test Item 1"
    expected_guid = (
        "tag:zissou:"
        + hashlib.sha256(
            f"http://example.com/article1|{mock_items[0].publishedAt.isoformat()}".encode(
                "utf-8"
            )
        ).hexdigest()
    )
    assert entry.id == expected_guid
    assert entry.link == "http://example.com/article1"
    assert entry.description == "This is a test."
    assert entry.published == "Fri, 08 Sep 2023 12:00:00 +0000"
    assert (
        item_elem.findtext("itunes:summary", default="", namespaces=ns)
        == "This is a test."
    )
    assert (
        item_elem.findtext("itunes:subtitle", default="", namespaces=ns)
        == "This is a test."
    )
    assert entry.itunes_author == "Tester"
    assert entry.itunes_duration == "00:02:03"
    assert (
        item_elem.findtext("itunes:keywords", default="", namespaces=ns)
        == "Test Bucket, news, tech"
    )
    assert (
        item_elem.find("itunes:image", namespaces=ns).attrib["href"]
        == "http://example.com/image1.png"
    )
    assert entry.published_parsed is not None
    tag_terms = (
        {tag["term"] for tag in entry.tags} if getattr(entry, "tags", None) else set()
    )
    assert {"Test Bucket", "news", "tech"}.issubset(tag_terms)

    # Enclosure checks
    assert len(entry.enclosures) == 1
    enclosure = entry.enclosures[0]
    assert enclosure.href == "https://storage.googleapis.com/test-bucket/audio1.mp3"
    assert enclosure.length == "123456"
    assert enclosure.type == "audio/mpeg"


def test_normalise_public_feed_filters_sanitises_inputs():
    filters = normalise_public_feed_filters(tag="  Science  ", days="730")
    assert filters == {"tag": "Science", "days": 365}

    assert normalise_public_feed_filters(tag="   ", days="7") == {"days": 7}
    assert normalise_public_feed_filters(days="-5") == {}
    assert normalise_public_feed_filters(days="not-a-number") == {}


def test_get_public_feed_items_raises_feed_index_error(monkeypatch):
    def explode(*args, **kwargs):
        raise FailedPrecondition("missing index")

    monkeypatch.setattr(feeds, "list_items", explode)
    monkeypatch.setattr(feeds, "extract_index_url", lambda exc: "https://console.example/index")

    with pytest.raises(FeedIndexBuildingError) as err:
        get_public_feed_items()

    assert err.value.hint == "https://console.example/index"


def test_filter_items_by_days_prefers_published_and_created_fallback():
    now = datetime.now(timezone.utc)
    recent_published = SimpleNamespace(
        publishedAt=now - timedelta(days=1),
        createdAt=now - timedelta(days=5),
    )
    recent_created = SimpleNamespace(
        publishedAt=None,
        createdAt=now - timedelta(days=1),
    )
    too_old = SimpleNamespace(
        publishedAt=now - timedelta(days=10),
        createdAt=now - timedelta(days=10),
    )
    no_dates = SimpleNamespace(
        publishedAt=None,
        createdAt=None,
    )

    filtered = _filter_items_by_days(
        [recent_published, recent_created, too_old, no_dates],
        days=3,
    )

    assert recent_published in filtered
    assert recent_created in filtered
    assert too_old not in filtered
    assert no_dates not in filtered


@patch("app.services.feeds.get_bucket_by_slug")
@patch("app.services.feeds.list_items")
@patch("app.services.feeds.list_buckets")
def test_generate_feed_with_require_audio_skips_items_without_audio(
    mock_list_buckets,
    mock_list_items,
    mock_get_bucket_by_slug,
):
    bucket = Bucket(
        id="bucket123",
        name="Audio Bucket",
        slug="audio-bucket",
        description="Bucket requiring audio.",
        rss_author_name="Narrator",
        rss_owner_email="narrator@example.com",
    )
    mock_get_bucket_by_slug.return_value = bucket
    mock_list_buckets.return_value = [bucket]

    now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    audio_item = Item(
        id="with-audio",
        title="With Audio",
        sourceUrl="https://example.com/with-audio",
        audioUrl="https://cdn.example.com/audio.mp3",
        audioSizeBytes=512000,
        durationSeconds=180.0,
        buckets=[bucket.id],
        publishedAt=now,
        createdAt=now,
    )
    silent_item = Item(
        id="without-audio",
        title="Without Audio",
        sourceUrl="https://example.com/without-audio",
        audioUrl="",
        buckets=[bucket.id],
        publishedAt=now,
        createdAt=now,
    )
    mock_list_items.return_value = ([audio_item, silent_item], None)

    feed_xml = feeds.generate_feed_for_bucket(
        "audio-bucket",
        "https://example.com/feeds/audio-bucket.xml",
        require_audio=True,
    )

    parsed = feedparser.parse(feed_xml)
    assert parsed.bozo == 0
    assert len(parsed.entries) == 1
    entry = parsed.entries[0]
    assert entry.title == "With Audio"
    assert entry.link == "https://example.com/with-audio"
    assert len(entry.enclosures) == 1
    assert entry.enclosures[0]["href"] == "https://cdn.example.com/audio.mp3"


@patch("app.services.feeds.get_bucket_by_slug")
@patch("app.services.feeds.list_items")
@patch("app.services.feeds.list_buckets")
def test_generate_feed_keywords_and_image_selection(
    mock_list_buckets,
    mock_list_items,
    mock_get_bucket_by_slug,
    monkeypatch,
):
    bucket = Bucket(
        id="bucket-keyword",
        name="Audio Bucket",
        slug="keyword-feed",
        description="Feed with keyword aggregation.",
        rss_author_name="Narrator",
        rss_owner_email="narrator@example.com",
        rss_cover_image_url="https://example.com/cover.webp",
    )
    mock_get_bucket_by_slug.return_value = bucket
    mock_list_buckets.return_value = [bucket]

    now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    item_one = Item(
        id="item-1",
        title="Alpha Story",
        sourceUrl="https://example.com/alpha",
        audioUrl="https://cdn.example.com/alpha.mp3",
        audioSizeBytes=256000,
        durationSeconds=90.0,
        tags=["news", "alpha"],
        buckets=[bucket.id],
        publishedAt=now,
        createdAt=now,
        updatedAt=now,
    )
    item_two = Item(
        id="item-2",
        title="Beta Story",
        sourceUrl="https://example.com/beta",
        audioUrl="https://cdn.example.com/beta.mp3",
        audioSizeBytes=512000,
        durationSeconds=120.0,
        tags=["news", "beta"],
        buckets=[bucket.id],
        publishedAt=now - timedelta(hours=1),
        createdAt=now - timedelta(hours=1),
        updatedAt=now - timedelta(hours=1),
    )
    mock_list_items.return_value = ([item_one, item_two], None)

    monkeypatch.setattr(feeds, "DEFAULT_FEED_IMAGE", "https://cdn.example.com/fallback.png")
    monkeypatch.setattr(feeds, "PUBLIC_FEED_IMAGE", None)

    feed_xml = feeds.generate_feed_for_bucket(
        "keyword-feed",
        "https://example.com/feeds/keyword-feed.xml",
    )

    root = ET.fromstring(feed_xml)
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    channel = root.find("channel")
    assert channel is not None

    channel_image = channel.find("itunes:image", namespaces=ns)
    assert channel_image is not None
    assert channel_image.attrib["href"] == "https://cdn.example.com/fallback.png"

    channel_keywords = channel.findtext("itunes:keywords", default="", namespaces=ns)
    assert channel_keywords.split(", ") == [
        "Audio Bucket",
        "alpha",
        "beta",
        "news",
    ]

    item_elements = channel.findall("item")
    assert len(item_elements) == 2

    first_keywords = item_elements[0].findtext("itunes:keywords", default="", namespaces=ns)
    second_keywords = item_elements[1].findtext("itunes:keywords", default="", namespaces=ns)

    assert first_keywords.split(", ") == ["Audio Bucket", "alpha", "news"]
    assert second_keywords.split(", ") == ["Audio Bucket", "beta", "news"]


@patch("app.services.feeds.get_bucket_by_slug")
@patch("app.services.feeds.list_items")
@patch("app.services.feeds.list_buckets")
def test_generate_feed_skips_invalid_episode_image(
    mock_list_buckets, mock_list_items, mock_get_bucket_by_slug
):
    fallback_url = "https://cdn.example.com/fallback.png"
    mock_bucket = Bucket(
        id="bucket123",
        name="Test Bucket",
        slug="test-bucket",
        description="Desc",
        rss_author_name="Tester",
        rss_owner_email="test@example.com",
        rss_cover_image_url="https://example.com/cover.gif",
    )
    mock_item = Item(
        id="item123",
        title="Test",
        sourceUrl="http://example.com/article",
        text="Body",
        durationSeconds=60,
        imageUrl="https://example.com/image.webp",
        createdAt=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    mock_get_bucket_by_slug.return_value = mock_bucket
    mock_list_buckets.return_value = [mock_bucket]
    mock_list_items.return_value = ([mock_item], None)

    with patch.object(feeds, "DEFAULT_EPISODE_IMAGE", fallback_url), patch.object(
        feeds, "DEFAULT_FEED_IMAGE", fallback_url
    ):
        feed_xml = generate_feed_for_bucket(
            "test-bucket", "https://example.com/feeds/test-bucket.xml"
        )

    root = ET.fromstring(feed_xml)
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    channel_elem = root.find("channel")
    assert channel_elem is not None
    channel_image = channel_elem.find("itunes:image", namespaces=ns)
    assert channel_image is not None
    assert channel_image.attrib["href"] == fallback_url

    item_elem = channel_elem.find("item")
    assert item_elem is not None
    item_image = item_elem.find("itunes:image", namespaces=ns)
    assert item_image is not None
    assert item_image.attrib["href"] == fallback_url
