from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Bucket:
    id: str | None = None
    name: str = ""
    slug: str = ""
    description: str = ""
    rss_author_name: str = ""
    rss_owner_email: str = ""
    rss_cover_image_url: str = ""
    itunes_categories: list[str] = field(default_factory=list)
    createdAt: datetime | None = None
    updatedAt: datetime | None = None
