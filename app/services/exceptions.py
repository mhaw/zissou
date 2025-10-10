from __future__ import annotations


class ExtractionError(Exception):
    """Base class for extraction pipeline errors."""

    def __init__(self, message: str, *, url: str | None = None) -> None:
        super().__init__(message)
        self.url = url


class NetworkError(ExtractionError):
    """Network instability while fetching a source document."""


class ParseError(ExtractionError):
    """HTML parsed but a downstream extractor failed to produce content."""


class TruncatedError(ExtractionError):
    """Extracted content is likely truncated or paywalled."""


class ArchiveTimeout(ExtractionError):
    """Archive fallbacks exceeded their time budget."""


__all__ = [
    "ExtractionError",
    "NetworkError",
    "ParseError",
    "TruncatedError",
    "ArchiveTimeout",
]
