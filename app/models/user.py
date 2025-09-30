from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass
class User:
    id: str | None = None
    email: str = ""
    name: str = ""
    role: str = "member"
    default_voice: str | None = None
    default_bucket_id: str | None = None
    articles_listened_to: int = 0
    total_listening_time: int = 0
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    def to_dict(self) -> dict:
        """Converts the user object to a dictionary, excluding id."""
        data = asdict(self)
        del data["id"]  # id is the document id, not part of the document data
        return data

    @classmethod
    def from_dict(cls, user_id: str, data: dict) -> User:
        """Creates a User object from a dictionary."""
        return cls(
            id=user_id,
            email=data.get("email", ""),
            name=data.get("name", ""),
            role=data.get("role", "member"),
            default_voice=data.get("default_voice"),
            default_bucket_id=data.get("default_bucket_id"),
            articles_listened_to=data.get("articles_listened_to", 0),
            total_listening_time=data.get("total_listening_time", 0),
            createdAt=data.get("createdAt"),
            updatedAt=data.get("updatedAt"),
        )
