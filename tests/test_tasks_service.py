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
