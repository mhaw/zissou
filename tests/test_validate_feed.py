import pytest

from tools.validate_feed import validate_feed


def _write_feed(tmp_path, content: str, filename: str = "feed.xml"):
    path = tmp_path / filename
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def _base_feed(enclosure_url: str = "https://example.com/audio.mp3", enclosure_type: str = "audio/mpeg") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <link>https://example.com</link>
    <description>Example description</description>
    <item>
      <title>Episode 1</title>
      <link>https://example.com/items/1</link>
      <guid>episode-1</guid>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
      <enclosure url="{enclosure_url}" length="123" type="{enclosure_type}" />
    </item>
  </channel>
</rss>"""


def test_validate_feed_raises_for_missing_entry_field(tmp_path):
    feed_xml = _base_feed().replace(
        "<link>https://example.com/items/1</link>\n      ", ""
    )
    feed_path = _write_feed(tmp_path, feed_xml, "missing_link.xml")

    with pytest.raises(ValueError, match="missing required field 'link'"):
        validate_feed(feed_path)


def test_validate_feed_rejects_non_audio_enclosure(tmp_path):
    feed_xml = _base_feed(enclosure_type="audio/wav")
    feed_path = _write_feed(tmp_path, feed_xml, "bad_type.xml")

    with pytest.raises(ValueError, match="enclosure type must be 'audio/mpeg'"):
        validate_feed(feed_path)


def test_validate_feed_rejects_relative_enclosure_url(tmp_path):
    feed_xml = _base_feed(enclosure_url="/audio.mp3")
    feed_path = _write_feed(tmp_path, feed_xml, "relative_url.xml")

    with pytest.raises(ValueError, match="enclosure URL must be absolute"):
        validate_feed(feed_path)


def test_validate_feed_rejects_malformed_xml(tmp_path):
    malformed_path = _write_feed(
        tmp_path,
        "<rss><channel><title>Broken</title><item></rss>",
        "broken.xml",
    )

    with pytest.raises(ValueError, match="Feed is not well-formed"):
        validate_feed(malformed_path)


def test_validate_feed_missing_file():
    with pytest.raises(FileNotFoundError):
        validate_feed("nonexistent_feed.xml")
