import io
from types import SimpleNamespace

from app.routes import tasks as tasks_routes


def test_process_article_task_creates_item(monkeypatch):
    update_calls: list[tuple[str, dict]] = []
    created_items: list[tuple[object, str | None]] = []
    uploaded_blobs: list[tuple[bytes, str, str | None]] = []

    monkeypatch.setattr(
        tasks_routes, "ensure_correlation_id", lambda value=None: "cid-test"
    )
    monkeypatch.setattr(tasks_routes, "bind_task_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tasks_routes, "bind_request_context", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(tasks_routes, "update_context", lambda *args, **kwargs: None)

    monkeypatch.setattr(
        tasks_routes.tasks_service,
        "update_task",
        lambda task_id, **fields: update_calls.append((task_id, fields)),
    )
    monkeypatch.setattr(
        tasks_routes.items_service,
        "find_item_by_source_url",
        lambda url: None,
    )
    monkeypatch.setattr(
        tasks_routes.items_service,
        "create_item",
        lambda item, user_id: created_items.append((item, user_id)) or "item-42",
    )
    monkeypatch.setattr(
        tasks_routes.items_service,
        "update_item_buckets",
        lambda item_id, bucket_ids: None,
    )
    monkeypatch.setattr(
        tasks_routes.smart_buckets_service, "list_smart_buckets", lambda: []
    )
    monkeypatch.setattr(
        tasks_routes.ai_enrichment,
        "maybe_schedule_enrichment",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        tasks_routes.buckets_service,
        "get_bucket",
        lambda bucket_id: SimpleNamespace(id=bucket_id, name="Inbox"),
    )
    monkeypatch.setattr(
        tasks_routes.parser,
        "extract_text",
        lambda url: {
            "text": "Hello world",
            "parser": "stub",
            "title": "Hello World",
            "author": "Example Author",
            "published_date": None,
            "image_url": None,
        },
    )
    monkeypatch.setattr(
        tasks_routes,
        "text_to_ssml_fragments",
        lambda *args, **kwargs: ["<speak>Hello</speak>"],
    )
    monkeypatch.setattr(
        tasks_routes.tts,
        "text_to_speech",
        lambda fragment, voice_name=None, use_ssml=True: (
            b"audio-chunk",
            None,
            "voice-profile",
        ),
    )
    monkeypatch.setattr(
        tasks_routes, "_normalize_audio_segment", lambda segment: segment
    )
    monkeypatch.setattr(
        tasks_routes,
        "get_audio_format_info",
        lambda: {
            "extension": "mp3",
            "pydub_format": "mp3",
            "content_type": "audio/mpeg",
        },
    )
    monkeypatch.setattr(
        tasks_routes.storage,
        "upload_to_gcs",
        lambda content, blob_name, content_type=None: uploaded_blobs.append(
            (content, blob_name, content_type)
        )
        or "https://storage.example/audio.mp3",
    )

    class StubAudioSegment:
        def __init__(self, duration=1000):
            self.duration = duration

        @staticmethod
        def empty():
            return StubAudioSegment(0)

        @staticmethod
        def from_file(filename, format):
            return StubAudioSegment(500)

        def __add__(self, other):
            return StubAudioSegment(self.duration + getattr(other, "duration", 0))

        def export(self, buffer, format):
            buffer.write(b"audio-output")

        def __len__(self):
            return self.duration

        @property
        def dBFS(self):
            return -10

        def apply_gain(self, gain):
            return self

    monkeypatch.setattr(tasks_routes, "AudioSegment", StubAudioSegment)

    tempfile_counter = {"value": 0}

    def fake_named_tempfile(delete=False, suffix=""):
        tempfile_counter["value"] += 1
        name = f"/tmp/fake-{tempfile_counter['value']}{suffix}"

        class DummyTempFile:
            def __init__(self, name):
                self.name = name
                self._buffer = io.BytesIO()

            def write(self, data):
                self._buffer.write(data)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return DummyTempFile(name)

    removed_files: list[str] = []

    monkeypatch.setattr(
        tasks_routes.tempfile, "NamedTemporaryFile", fake_named_tempfile
    )
    monkeypatch.setattr(
        tasks_routes.os, "remove", lambda path: removed_files.append(path)
    )

    tasks_routes.process_article_task(
        "task-1",
        "https://example.com/article",
        voice="voice-1",
        bucket_id="bucket-1",
        user_id="user-1",
    )

    # Ensure status transitions included completion update
    assert any(
        fields.get("status") == "COMPLETED" and fields.get("item_id") == "item-42"
        for task, fields in update_calls
        if task == "task-1"
    )

    # Ensure an item was written to Firestore via create_item
    assert created_items, "expected create_item to be invoked"
    created_item, created_user_id = created_items[0]
    assert created_user_id == "user-1"
    assert created_item.title == "Hello World"
    assert created_item.voiceSetting == "voice-profile"

    # Upload should receive combined audio payload
    assert uploaded_blobs, "expected audio upload to occur"
    _, blob_name, content_type = uploaded_blobs[0]
    assert blob_name.startswith("audio/")
    assert content_type == "audio/mpeg"
