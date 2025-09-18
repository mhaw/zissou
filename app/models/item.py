from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Item:
    id: str = None
    title: str = ""
    sourceUrl: str = ""
    author: str = ""
    publishedAt: datetime = None  # New field for publication date
    imageUrl: str = None  # New field for main image URL
    text: str = ""
    audioUrl: str = ""
    audioSizeBytes: int = 0
    durationSeconds: float = 0.0
    buckets: list[str] = field(default_factory=list)
    createdAt: datetime = field(default_factory=datetime.utcnow)
    updatedAt: datetime = field(default_factory=datetime.utcnow)

    # Processing metrics
    processingTimeMs: int = 0
    voiceSetting: str = ""
    pipelineTools: list[str] = field(default_factory=list)
    parsingTimeMs: int = 0
    ttsTimeMs: int = 0
    uploadTimeMs: int = 0
    chunkCount: int = 0
    textLength: int = 0
    tags: list[str] = field(default_factory=list)

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
