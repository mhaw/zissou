from app.services import storage


def test_extract_blob_name_public_url(monkeypatch):
    monkeypatch.setenv("GCS_BUCKET", "example-bucket")
    url = "https://storage.googleapis.com/example-bucket/audio/sample.mp3"
    assert storage.extract_blob_name(url) == "audio/sample.mp3"


def test_extract_blob_name_gs_url(monkeypatch):
    monkeypatch.setenv("GCS_BUCKET", "example-bucket")
    url = "gs://example-bucket/audio/sample.mp3"
    assert storage.extract_blob_name(url) == "audio/sample.mp3"


def test_extract_blob_name_invalid_bucket(monkeypatch):
    monkeypatch.setenv("GCS_BUCKET", "example-bucket")
    url = "https://storage.googleapis.com/other-bucket/audio/sample.mp3"
    assert storage.extract_blob_name(url) is None


def test_extract_blob_name_missing_bucket_env(monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    assert (
        storage.extract_blob_name(
            "https://storage.googleapis.com/example-bucket/audio/sample.mp3"
        )
        is None
    )
