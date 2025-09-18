import json

import pytest

from app.services import readwise

SAMPLE_JSON = {
    "props": {
        "pageProps": {
            "share": {"title": "Sample Readwise Share"},
            "entries": [
                {
                    "id": "abc",
                    "title": "First Article",
                    "source_url": "https://example.com/one",
                    "author": "Pat Writer",
                    "site": "Example",
                },
                {
                    "id": "def",
                    "document_title": "Second Article",
                    "url": "https://example.com/two",
                },
            ],
        }
    }
}

SAMPLE_HTML_WITH_JSON = f"""
<html>
<head><title>Readwise • Shared</title></head>
<body>
<script id="__NEXT_DATA__" type="application/json">{json.dumps(SAMPLE_JSON)}</script>
</body>
</html>
"""

SAMPLE_HTML_WITH_LINKS = """
<html>
<head><title>Manual Share</title></head>
<body>
  <div data-reading-item-id="1"><a href="https://example.org/alpha">Alpha Piece</a></div>
  <div><a data-reading-item-url="https://example.org/bravo">Bravo Piece</a></div>
</body>
</html>
"""


class DummyResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class DummySession:
    def __init__(self, response):
        self._response = response

    def get(self, *_args, **_kwargs):
        return self._response


def test_parse_shared_view_via_next_data():
    session = DummySession(DummyResponse(SAMPLE_HTML_WITH_JSON))
    result = readwise.fetch_shared_view(
        "https://share.readwise.io/test", session=session
    )
    assert result["title"] == "Readwise • Shared"
    urls = {article.url for article in result["articles"]}
    assert urls == {"https://example.com/one", "https://example.com/two"}


def test_parse_shared_view_via_dom_links():
    session = DummySession(DummyResponse(SAMPLE_HTML_WITH_LINKS))
    result = readwise.fetch_shared_view(
        "https://share.readwise.io/test", session=session
    )
    titles = [article.title for article in result["articles"]]
    assert titles == ["Alpha Piece", "Bravo Piece"]


def test_invalid_url_errors():
    with pytest.raises(readwise.ReadwiseImportError):
        readwise.fetch_shared_view("not-a-url")


def test_http_error_raises():
    session = DummySession(DummyResponse("oops", status_code=404))
    with pytest.raises(readwise.ReadwiseImportError):
        readwise.fetch_shared_view("https://share.readwise.io/test", session=session)
