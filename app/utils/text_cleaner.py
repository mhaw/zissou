"""Helpers to normalise extracted article text."""

import html
import re
import unicodedata
from typing import Iterable

# Common boilerplate phrases to strip from extracted article text.
_BOILERPLATE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^advertisement$",
        r"^sponsored content$",
        r"^sign up for our newsletter.*",
        r"^subscribe to .*",
        r"^related (stories|articles).*",
        r"^read (more|next):.*",
        r"^share this (story|article).*",
        r"^follow us on .*",
        r"^comments?$",
    )
)

_CONTROL_CHARS = {
    "\u200b",  # zero-width space
    "\u200c",  # zero-width non-joiner
    "\u200d",  # zero-width joiner
    "\ufeff",  # zero-width no-break space / BOM
}

_HEADING_PREFIX = re.compile(r"^##\s+")
_LIST_PREFIX = re.compile(r"^-\s+")


def _strip_control_chars(text: str) -> str:
    for char in _CONTROL_CHARS:
        text = text.replace(char, "")
    return text


def _remove_boilerplate(lines: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append("")
            continue
        if any(pattern.match(stripped) for pattern in _BOILERPLATE_PATTERNS):
            continue
        cleaned.append(stripped)
    return cleaned


def clean_text(raw_text: str | None) -> str:
    """Normalise extracted article text and remove obvious boilerplate."""
    if not raw_text:
        return ""

    text = html.unescape(raw_text)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = _strip_control_chars(text)
    text = re.sub(r"[\t\f]+", " ", text)
    text = re.sub(r" {2,}", " ", text)

    lines = text.split("\n")
    lines = _remove_boilerplate(lines)

    # Collapse sequences of more than two blank lines.
    normalised_lines: list[str] = []
    for line in lines:
        if not line:
            if normalised_lines and normalised_lines[-1] == "":
                continue
            normalised_lines.append("")
        else:
            if _HEADING_PREFIX.match(line) and normalised_lines and normalised_lines[-1]:
                normalised_lines.append("")
            if (
                _LIST_PREFIX.match(line)
                and normalised_lines
                and normalised_lines[-1]
                and not _LIST_PREFIX.match(normalised_lines[-1])
            ):
                normalised_lines.append("")
            normalised_lines.append(line)

    cleaned_text = "\n".join(normalised_lines).strip()
    # Ensure paragraphs are separated by a single blank line
    cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)
    return cleaned_text
