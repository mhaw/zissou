import json
import time
from types import SimpleNamespace

import pytest

from app.services import archive_utils, fetch


class FakeResponse:
    def __init__(
        self, status_code=200, text="", url="https://example.com", headers=None
    ):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            from requests import HTTPError

            raise HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def get(self, url, headers, timeout, allow_redirects):
        call_number = len(self.calls)
        self.calls.append(SimpleNamespace(url=url, headers=headers, timeout=timeout))
        response = self._responses[call_number]
        if isinstance(response, Exception):
            raise response
        return response


def test_fetch_with_resilience_retries_and_succeeds():
    responses = [
        FakeResponse(status_code=503, url="https://example.com"),
        FakeResponse(status_code=200, text="ok", url="https://example.com/article"),
    ]
    session = FakeSession(responses)
    waits = []

    result = fetch.fetch_with_resilience(
        "https://example.com/article",
        session=session,
        sleep=waits.append,
    )

    assert result["html"] == "ok"
    assert session.calls and len(session.calls) == 2
    assert waits, "Expected at least one backoff sleep"


def test_fetch_with_resilience_honors_retry_after_header():
    waits = []
    responses = [
        FakeResponse(
            status_code=429, url="https://example.com", headers={"Retry-After": "1"}
        ),
        FakeResponse(status_code=200, text="done", url="https://example.com/article"),
    ]
    session = FakeSession(responses)

    result = fetch.fetch_with_resilience(
        "https://example.com/article",
        session=session,
        sleep=waits.append,
    )

    assert result["html"] == "done"
    assert waits[0] == pytest.approx(1.0)


def test_recover_truncated_content_prefers_archive_today():
    archive_utils._failure_cache.clear()
    truncated_text = "short"
    archive_url = "https://archive.today/latest/https://example.com/article"
    responses = {
        archive_url: {
            "html": "<html><body>Full article content</body></html>",
            "final_url": "https://archive.today/abc123",
            "status_code": 200,
        }
    }

    def fetcher(url):
        return responses.get(url, {"error": "not found"})

    def extractor(html, origin_url, resolved_url):
        return {
            "text": "A" * 800,
            "source_url": origin_url,
            "resolved_url": resolved_url,
        }

    result = archive_utils.recover_truncated_content(
        "https://example.com/article",
        truncated_text,
        extractor=extractor,
        fetcher=fetcher,
        is_truncated=lambda text: len((text or "").strip()) < 500,
    )

    assert result
    assert result["fetched_via"] == "archive.today"
    assert result["archive_snapshot_url"] == "https://archive.today/abc123"
    assert result["source_url"] == "https://example.com/article"


def test_recover_truncated_content_falls_back_to_wayback():
    archive_utils._failure_cache.clear()
    truncated_text = "short"
    wayback_api = (
        "https://archive.org/wayback/available?url=https%3A%2F%2Fexample.com%2Farticle"
    )
    snapshot_url = (
        "https://web.archive.org/web/20230101000000/https://example.com/article"
    )

    call_order = []

    def fetcher(url):
        call_order.append(url)
        if url.startswith("https://archive.today"):
            return {"error": "missing"}
        if url == wayback_api:
            payload = {
                "archived_snapshots": {
                    "closest": {
                        "url": snapshot_url,
                    }
                }
            }
            return {
                "html": json.dumps(payload),
                "final_url": wayback_api,
                "status_code": 200,
            }
        if url == snapshot_url:
            return {
                "html": "<html><body>Recovered text</body></html>",
                "final_url": snapshot_url,
                "status_code": 200,
            }
        return {"error": "unexpected"}

    def extractor(html, origin_url, resolved_url):
        if resolved_url == snapshot_url:
            return {
                "text": "Recovered text " + ("A" * 600),
                "source_url": origin_url,
                "resolved_url": resolved_url,
            }
        return {"error": "parse error"}

    result = archive_utils.recover_truncated_content(
        "https://example.com/article",
        truncated_text,
        extractor=extractor,
        fetcher=fetcher,
        is_truncated=lambda text: len((text or "").strip()) < 500,
    )

    assert result
    assert result["fetched_via"] == "wayback"
    assert result["archive_snapshot_url"] == snapshot_url
    assert any(url.startswith("https://archive.today") for url in call_order)
    assert wayback_api in call_order
    assert snapshot_url in call_order


def test_archive_recovery_caches_failure(monkeypatch):
    archive_utils._failure_cache.clear()

    call_counter = {"count": 0}

    def fetcher(url):
        call_counter["count"] += 1
        return {"error": "nope"}

    def extractor(html, origin_url, resolved_url):
        return {"error": "still truncated"}

    result = archive_utils.recover_truncated_content(
        "https://example.com/cache",
        "short",
        extractor=extractor,
        fetcher=fetcher,
        is_truncated=lambda _: True,
    )

    assert result is None
    first_calls = call_counter["count"]
    assert first_calls > 0

    second = archive_utils.recover_truncated_content(
        "https://example.com/cache",
        "short",
        extractor=extractor,
        fetcher=fetcher,
        is_truncated=lambda _: True,
    )

    assert second is None
    assert call_counter["count"] == first_calls
    archive_utils._failure_cache.clear()


def test_archive_recovery_times_out(monkeypatch):
    archive_utils._failure_cache.clear()
    monkeypatch.setattr(archive_utils, "ARCHIVE_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(archive_utils, "_should_skip_archive", lambda url: False)

    def slow_fetcher(url):
        time.sleep(0.05)
        return {"error": "slow"}

    def extractor(html, origin_url, resolved_url):
        return {"error": "still truncated"}

    start = time.perf_counter()
    result = archive_utils.recover_truncated_content(
        "https://example.com/timeout",
        "short",
        extractor=extractor,
        fetcher=slow_fetcher,
        is_truncated=lambda _: True,
    )
    duration = time.perf_counter() - start

    assert result is None
    # Ensure we respected the timeout budget (allowing for small scheduler overhead)
    assert duration < 0.2
    archive_utils._failure_cache.clear()
