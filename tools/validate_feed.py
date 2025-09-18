import sys
import feedparser
import logging

logging.basicConfig(level=logging.INFO)

if len(sys.argv) < 2:
    print("Usage: python tools/validate_feed.py <path_to_feed.xml>")
    sys.exit(1)

file_path = sys.argv[1]
logging.info(f"Validating feed: {file_path}")

d = feedparser.parse(file_path)

assert d.bozo == 0, f"Feed is not well-formed. Error: {d.bozo_exception}"
logging.info("Feed is well-formed XML.")

assert d.entries, "Feed contains no entries."
logging.info(f"Found {len(d.entries)} entries.")

for i, entry in enumerate(d.entries):
    logging.info(f"-- Checking Entry {i+1} --")
    required_fields = ["title", "id", "link", "published_parsed", "enclosures"]
    for field in required_fields:
        assert hasattr(
            entry, field
        ), f"Entry {i+1} is missing required field: '{field}'"
        assert getattr(
            entry, field
        ), f"Entry {i+1} has an empty required field: '{field}'"

    assert len(entry.enclosures) == 1, f"Entry {i+1} must have exactly one enclosure."
    enclosure = entry.enclosures[0]
    assert (
        enclosure.get("type") == "audio/mpeg"
    ), f"Enclosure type must be 'audio/mpeg', not '{enclosure.get('type')}'"
    assert enclosure.get("href", "").startswith(
        "http"
    ), f"Enclosure URL must be absolute. Got: {enclosure.get('href')}"
    logging.info(f"Entry '{entry.title}' passed all checks.")

print("\nValidation successful! All entries have the required fields and structure.")
