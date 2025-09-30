from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Task:
    """Represents a background task for processing an article."""

    sourceUrl: str
    status: str = "QUEUED"
    createdAt: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updatedAt: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: Optional[str] = None
    item_id: Optional[str] = None
    error: Optional[str] = None
    errorCode: Optional[str] = None
    voice: Optional[str] = None
    bucket_id: Optional[str] = None
    retryCount: int = 0
    userId: str | None = None

    def to_dict(self):
        """Convert dataclass to a dictionary, excluding None values."""
        return asdict(self)

    @classmethod
    def from_dict(cls, doc_id, data):
        """Create a Task instance from a Firestore document."""
        # Ensure datetime fields are converted from Firestore Timestamps
        for date_field in ["createdAt", "updatedAt"]:
            if date_field not in data:
                continue
            value = data[date_field]
            if hasattr(value, "to_datetime"):
                value = value.to_datetime()
            if isinstance(value, datetime) and value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            data[date_field] = value

        # The id from the document might be in the data dict, so we remove it
        # to avoid passing it twice to the constructor.
        data.pop("id", None)

        return Task(id=doc_id, **data)
