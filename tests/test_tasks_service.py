from app.services import tasks as tasks_service


class FakeExistingDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class FakeDocument:
    def __init__(self, doc_id, created_store):
        self.id = doc_id
        self._created_store = created_store

    def set(self, payload):
        self._created_store.append((self.id, payload))


class FakeQuery:
    def __init__(self, docs):
        self._docs = docs
        self._limit = len(docs)

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, count):
        self._limit = count
        return self

    def stream(self):
        return iter(self._docs[: self._limit])


class FakeCollection:
    def __init__(self, docs):
        self.docs = docs
        self.created = []
        self._counter = 0

    def where(self, *, filter):  # type: ignore[override]
        return FakeQuery(self.docs)

    def document(self):
        self._counter += 1
        return FakeDocument(f"generated-{self._counter}", self.created)


class FakeDB:
    def __init__(self, collection):
        self._collection = collection

    def collection(self, name):  # type: ignore[override]
        return self._collection


def test_create_task_reuses_active_task(monkeypatch):
    existing = FakeExistingDoc(
        "task-123",
        {
            "status": "PROCESSING",
            "voice": "captains-log",
            "bucket_id": None,
        },
    )
    fake_collection = FakeCollection([existing])
    fake_db = FakeDB(fake_collection)

    monkeypatch.setattr(tasks_service, "db", fake_db, raising=False)
    monkeypatch.setenv("ENV", "production")

    task_id = tasks_service.create_task(
        "https://example.com/article", voice="captains-log"
    )

    assert task_id == "task-123"
    assert fake_collection.created == []


def test_create_task_persists_document_and_enqueues(monkeypatch):
    fake_collection = FakeCollection([])
    fake_db = FakeDB(fake_collection)

    monkeypatch.setattr(tasks_service, "db", fake_db, raising=False)
    monkeypatch.setenv("ENV", "production")

    recorded_payloads: list[dict] = []

    monkeypatch.setattr(tasks_service, "create_cloud_task", recorded_payloads.append)
    monkeypatch.setattr(
        tasks_service,
        "ensure_correlation_id",
        lambda value=None: "cid-test",
        raising=False,
    )
    monkeypatch.setattr(
        tasks_service, "bind_request_context", lambda **kwargs: None, raising=False
    )
    monkeypatch.setattr(
        tasks_service, "bind_task_context", lambda **kwargs: None, raising=False
    )
    monkeypatch.setattr(
        tasks_service, "update_context", lambda **kwargs: None, raising=False
    )

    task_id = tasks_service.create_task(
        "https://example.com/article",
        voice="captains-log",
        bucket_id="bucket-1",
        user={"uid": "user-1"},
    )

    assert task_id.startswith("generated-")
    assert fake_collection.created, "expected Firestore document to be created"
    created_id, created_payload = fake_collection.created[0]
    assert created_id == task_id
    assert created_payload["status"] == "QUEUED"
    assert created_payload["voice"] == "captains-log"
    assert created_payload["bucket_id"] == "bucket-1"
    assert created_payload["userId"] == "user-1"

    assert recorded_payloads, "expected Cloud Task creation payload"
    queued_payload = recorded_payloads[0]
    assert queued_payload["task_id"] == task_id
    assert queued_payload["url"] == "https://example.com/article"
    assert queued_payload["correlation_id"] == "cid-test"
