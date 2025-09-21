from __future__ import annotations
from dataclasses import dataclass, field
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
