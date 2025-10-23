import json
from types import SimpleNamespace

import app.services.ai_enrichment as ai_enrichment


def test_fallback_tags_honors_frequency_and_limit(monkeypatch):
    # Ensure the fallback respects the configured limit and frequency ordering.
    monkeypatch.setattr(ai_enrichment, "_TAG_LIMIT", 4)

    text = """
    The Analytics platform delivers analytics insights daily.
    Analytics teams and insights systems build PLATFORM strategy.
    Systems pair INSIGHTS with analytics powered growth. Analytics.
    """

    tags = ai_enrichment._fallback_tags(text)

    assert tags == ["Analytics", "Insights", "Platform", "Systems"]
    assert len(tags) == 4
    assert "The" not in tags


def test_maybe_schedule_enrichment_skips_when_enriched(monkeypatch):
    monkeypatch.setenv("ENABLE_SUMMARY", "true")
    monkeypatch.setenv("ENABLE_AUTO_TAGS", "true")

    submitted = []

    def fake_submit(*args, **kwargs):
        submitted.append((args, kwargs))

    monkeypatch.setattr(
        ai_enrichment.items_service,
        "get_item",
        lambda _: SimpleNamespace(summary_text="done", auto_tags=["tag"]),
    )
    monkeypatch.setattr(ai_enrichment._executor, "submit", fake_submit)

    ai_enrichment.maybe_schedule_enrichment("item-123", "Some article text", "cid-1")

    assert submitted == []


def test_parse_structured_response_handles_candidate_list(monkeypatch):
    monkeypatch.setattr(ai_enrichment, "_TAG_LIMIT", 5)
    payload = {"summary": "Insightful overview", "tags": ["AI", "ai", "Data", 123]}
    raw = [SimpleNamespace(text=json.dumps(payload))]

    summary, tags = ai_enrichment._parse_structured_response(raw)

    assert summary == "Insightful overview"
    assert tags == ["AI", "Data"]
