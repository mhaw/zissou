from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class Task:
    """Represents a background task for processing an article."""

    sourceUrl: str
    status: str = "PENDING"
    createdAt: datetime = field(default_factory=datetime.utcnow)
    updatedAt: datetime = field(default_factory=datetime.utcnow)
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
            if date_field in data and hasattr(data[date_field], "to_datetime"):
                data[date_field] = data[date_field].to_datetime()

        # The id from the document might be in the data dict, so we remove it
        # to avoid passing it twice to the constructor.
        data.pop("id", None)

        return Task(id=doc_id, **data)
