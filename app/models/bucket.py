from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from app.services.firestore_helpers import normalise_timestamp

logger = logging.getLogger(__name__)


@dataclass
class Bucket:
    id: str | None = None
    name: str = ""
    slug: str = ""
    description: str = ""
    rss_author_name: str | None = ""
    rss_owner_email: str | None = ""
    rss_cover_image_url: str | None = ""
    itunes_categories: list[str] = field(default_factory=list)
    is_public: bool = False
    public: bool = False
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Bucket":
        # Filter out unexpected fields to prevent errors
        bucket_fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered_data = {k: v for k, v in data.items() if k in bucket_fields}

        # Normalize date fields
        for date_field in ["createdAt", "updatedAt"]:
            if date_field in filtered_data:
                raw_value = filtered_data[date_field]
                normalised = normalise_timestamp(raw_value)
                if normalised is None and raw_value:
                    logger.warning(
                        "Could not normalise date value '%s' for field '%s'. Leaving as None.",
                        raw_value,
                        date_field,
                    )
                filtered_data[date_field] = normalised

        return cls(**filtered_data)
