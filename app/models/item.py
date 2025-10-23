from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from app.services.firestore_helpers import normalise_timestamp

logger = logging.getLogger(__name__)


@dataclass
class Item:
    id: str | None = None
    title: str = ""
    sourceUrl: str = ""
    author: str = ""
    publishedAt: datetime | None = None  # New field for publication date
    imageUrl: str | None = None  # New field for main image URL
    text: str = ""
    summary_text: str | None = None
    audioUrl: str = ""
    audioSizeBytes: int = 0
    durationSeconds: float = 0.0
    buckets: list[str] = field(default_factory=list)
    createdAt: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updatedAt: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Processing metrics
    processingTimeMs: int = 0
    voiceSetting: str = ""
    pipelineTools: list[str] = field(default_factory=list)
    parsingTimeMs: int = 0
    ttsTimeMs: int = 0
    uploadTimeMs: int = 0
    chunkCount: int = 0
    textLength: int = 0
    reading_time: int = 0
    tags: list[str] = field(default_factory=list)
    auto_tags: list[str] = field(default_factory=list)
    is_archived: bool = False
    is_read: bool = False
    userId: str | None = None
    is_public: bool = False

    @property
    def source_url(self) -> str | None:
        return getattr(self, "sourceUrl", None)

    @property
    def audio_url(self) -> str | None:
        return getattr(self, "audioUrl", None)

    @property
    def enclosure_url(self) -> str | None:
        # RSS enclosure typically mirrors the primary audio asset.
        return getattr(self, "audioUrl", None)

    @property
    def image_url(self) -> str | None:
        return getattr(self, "imageUrl", None)

    @classmethod
    def from_dict(cls, data: dict) -> "Item":
        # Filter out unexpected fields to prevent errors
        item_fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered_data = {k: v for k, v in data.items() if k in item_fields}

        # Normalize date fields
        for date_field in ["publishedAt", "createdAt", "updatedAt"]:
            if date_field in filtered_data:
                raw_value = filtered_data[date_field]
                normalised = normalise_timestamp(raw_value)
                if normalised is None and raw_value:
                    # Log a warning if normalization fails but raw value exists
                    # (This logging might need to be handled outside the model if logger is not available)
                    pass
                filtered_data[date_field] = normalised

        return cls(**filtered_data)
