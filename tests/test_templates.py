from datetime import datetime, timezone
from types import SimpleNamespace

from flask import render_template


def _sample_item():
    return SimpleNamespace(
        id="item-1",
        title="Sample Item",
        author="Test Author",
        sourceUrl="https://example.com/sample",
        imageUrl=None,
        audioUrl=None,
        audioMimeType=None,
        audioSizeBytes=0,
        durationSeconds=0,
        buckets=["bucket-a"],
        is_archived=False,
        tags=["ManualOne"],
        auto_tags=["AutoOne"],
        createdAt=datetime.now(timezone.utc),
        updatedAt=datetime.now(timezone.utc),
        publishedAt=None,
        text="Example body text.",
        summary_text=None,
        reading_time=0,
        is_read=False,
        voiceSetting="",
        processingTimeMs=0,
        pipelineTools=[],
        parsingTimeMs=0,
        ttsTimeMs=0,
        uploadTimeMs=0,
        chunkCount=0,
        textLength=0,
    )


def test_item_detail_renders_tag_summary(app):
    item = _sample_item()

    with app.test_request_context(f"/items/{item.id}"):
        rendered = render_template(
            "item_detail.html",
            item=item,
            buckets=[],
            bucket_lookup={},
            bucket_options=[],
            all_tags=item.tags,
            manual_tags=item.tags,
            auto_tags=item.auto_tags,
            enable_summary=False,
            enable_auto_tags=True,
            task=None,
            can_edit=True,
            back_url="/items",
            back_label="Back to List",
        )

    assert "ManualOne" in rendered
    assert "AutoOne" in rendered


def test_item_detail_public_view_hides_admin_controls(app):
    item = _sample_item()

    with app.test_request_context(f"/items/{item.id}"):
        rendered = render_template(
            "item_detail.html",
            item=item,
            buckets=[],
            bucket_lookup={},
            bucket_options=[],
            all_tags=item.tags,
            manual_tags=item.tags,
            auto_tags=item.auto_tags,
            enable_summary=False,
            enable_auto_tags=True,
            task=None,
            can_edit=False,
            back_url="/feeds/public",
            back_label="Browse Feed",
        )

    assert "Processing Metrics" not in rendered
    assert "Assign to Buckets" not in rendered
    assert "Manage Tags" not in rendered
