import pytest
from datetime import datetime, timezone
import hashlib
from unittest.mock import patch
import feedparser
from xml.etree import ElementTree as ET

from app.services.feeds import _coerce_datetime, generate_feed_for_bucket
import app.services.feeds as feeds
from app.models.item import Item
from app.models.bucket import Bucket


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
    mock_list_buckets, mock_list_items, mock_get_bucket_by_slug
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
