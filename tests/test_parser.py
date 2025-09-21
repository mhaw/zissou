from app.services.parser import extract_text
from app.utils.text_cleaner import clean_text

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:  # pragma: no cover - optional dependency in tests
    BeautifulSoup = None  # type: ignore[assignment]

from app.services import parser as parser_module


def test_extract_text_returns_processed_content(monkeypatch):
    def fake_fetch(url, user_agent=None):
        return {"html": "<html></html>", "final_url": url}

    def fake_process(html, origin_url, resolved_url=None):
        return {
            "title": "Test Title",
            "author": "Author",
            "text": "Clean text",
            "source_url": origin_url,
            "parser": "fake",
        }

    monkeypatch.setattr("app.services.parser.fetch_with_resilience", fake_fetch)
    monkeypatch.setattr("app.services.parser._process_html", fake_process)
    monkeypatch.setattr("app.services.parser.is_likely_truncated", lambda text: False)

    result = extract_text("http://example.com/article")
    assert result["title"] == "Test Title"
    assert result["text"] == "Clean text"
    assert result["source_url"] == "http://example.com/article"


def test_extract_text_propagates_fetch_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.parser.fetch_with_resilience",
        lambda url, user_agent=None: {"error": "timeout"},
    )

    result = extract_text("http://example.com/article")
    assert result["error"] == "timeout"


def test_extract_text_hybrid_retry_success(monkeypatch):
    def fake_fetch(url, user_agent=None, extra_headers=None, **kwargs):
        return {"html": "initial", "final_url": url}

    def fake_process(html, origin_url, resolved_url=None):
        if html == "initial":
            return {
                "title": "Initial",
                "author": "Author",
                "text": "short",
                "source_url": origin_url,
                "parser": "fake",
            }
        return {
            "title": "Improved",
            "author": "Author",
            "text": "x" * 600,
            "source_url": origin_url,
            "parser": "fake",
            "extractor_metrics": {"winner": "fake"},
        }

    def fake_hybrid_attempts(url, user_agent=None, **kwargs):
        yield {"Referer": "https://news.google.com/"}, {
            "html": "hybrid",
            "final_url": url,
        }

    monkeypatch.setattr("app.services.parser.fetch_with_resilience", fake_fetch)
    monkeypatch.setattr("app.services.parser._process_html", fake_process)
    monkeypatch.setattr(
        "app.services.parser.hybrid_fetch_attempts", fake_hybrid_attempts
    )
    monkeypatch.setattr(
        "app.services.parser.recover_truncated_content", lambda *args, **kwargs: None
    )

    result = extract_text("http://example.com/article")

    assert result["text"] == "x" * 600
    assert result["fetched_via"] == "direct-hybrid"
    assert result["fetch_profile"]["Referer"] == "https://news.google.com/"


def test_extract_text_prefers_longest_engine_output(monkeypatch):
    monkeypatch.setattr(
        "app.services.parser.fetch_with_resilience",
        lambda url, user_agent=None, **kwargs: {"html": "payload", "final_url": url},
    )
    monkeypatch.setattr("app.services.parser.is_likely_truncated", lambda text: False)

    monkeypatch.setattr(
        "app.services.parser._extract_with_trafilatura",
        lambda url, html, source_url=None: {
            "text": "x" * 200,
            "parser": "trafilatura",
            "source_url": source_url or url,
            "resolved_url": url,
        },
    )

    monkeypatch.setattr(
        "app.services.parser._extract_with_newspaper",
        lambda url, html, source_url=None: {
            "text": "y" * 600,
            "parser": "newspaper3k",
            "source_url": source_url or url,
            "resolved_url": url,
        },
    )

    monkeypatch.setattr(
        "app.services.parser._extract_with_readability",
        lambda url, html, source_url=None: {
            "text": "z" * 1600,
            "parser": "readability",
            "source_url": source_url or url,
            "resolved_url": url,
        },
    )

    monkeypatch.setattr(
        "app.services.parser._extract_with_soup_heuristic",
        lambda url, html, source_url=None: {
            "text": "w" * 800,
            "parser": "soup_heuristic",
            "source_url": source_url or url,
            "resolved_url": url,
        },
    )

    result = extract_text("http://example.com/article")

    assert result["parser"] == "readability"
    assert len(result["text"]) == 1600


def test_clean_text_normalises_whitespace_and_boilerplate():
    raw = "Line\u00a0one\n\nAdvertisement\n\nLine two\u200b"  # includes NBSP and zero-width space
    cleaned = clean_text(raw)
    assert cleaned == "Line one\n\nLine two"


def test_clean_text_collapses_blank_lines():
    raw = "First paragraph\n\n\n\nSecond paragraph\n\nRelated Articles\nThird paragraph"
    cleaned = clean_text(raw)
    assert cleaned == "First paragraph\n\nSecond paragraph\n\nThird paragraph"


def test_collect_paragraphs_captures_content_beyond_ads():
    if BeautifulSoup is None:
        return

    leading = "A" * 520
    trailing = "B" * 200
    html = f"""
        <html>
            <body>
                <article>
                    <p>{leading}</p>
                    <div class=\"ad-container\">
                        <p>Advertisement</p>
                    </div>
                    <p>{trailing}</p>
                </article>
            </body>
        </html>
    """

    soup = BeautifulSoup(html, "lxml")
    paragraphs = parser_module._collect_paragraphs(soup)

    assert len(paragraphs) == 2
    assert paragraphs[0] == leading
    assert paragraphs[1] == trailing


def test_extract_with_trafilatura_handles_new_metadata_signature(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        parser_module.trafilatura,
        "extract",
        lambda *args, **kwargs: " Sample body text ",
    )

    captured = {}

    def fake_extract_metadata(filecontent, default_url=None, **kwargs):
        captured["default_url"] = default_url
        return SimpleNamespace(
            title="Title",
            author="Author",
            date="2024-01-01",
            image="https://example.com/cover.jpg",
        )

    monkeypatch.setattr(
        parser_module.trafilatura, "extract_metadata", fake_extract_metadata
    )

    result = parser_module._extract_with_trafilatura(
        "http://example.com/article",
        html="<html></html>",
        source_url="http://example.com/article",
    )

    assert result["title"] == "Title"
    assert result["author"] == "Author"
    assert result["image_url"] == "https://example.com/cover.jpg"
    assert captured["default_url"] == "http://example.com/article"
