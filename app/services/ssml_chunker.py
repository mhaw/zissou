from __future__ import annotations
import logging
import os
import re
from typing import Callable, Iterable, List, Optional

logger = logging.getLogger(__name__)

GOOGLE_TTS_MAX_INPUT_BYTES = int(
    os.getenv("GOOGLE_TTS_MAX_INPUT_BYTES") or os.getenv("TTS_REQUEST_BYTE_LIMIT", "5000")
)
TTS_SAFETY_MARGIN_BYTES = int(os.getenv("TTS_SAFETY_MARGIN_BYTES", "400"))
TTS_MIN_CHUNK_BYTES = int(os.getenv("TTS_MIN_CHUNK_BYTES", "600"))
_raw_tts_chunk_bytes = int(os.getenv("TTS_MAX_CHUNK_BYTES", "4800"))
_effective_limit = max(256, GOOGLE_TTS_MAX_INPUT_BYTES - TTS_SAFETY_MARGIN_BYTES)
MAX_TTS_CHUNK_BYTES = max(
    TTS_MIN_CHUNK_BYTES,
    min(_raw_tts_chunk_bytes, _effective_limit),
)


class SSMLChunkingError(RuntimeError):
    """Raised when SSML fragments cannot be generated within API limits."""


def _split_by_bytes(text: str, max_bytes: int) -> List[str]:
    parts: List[str] = []
    current = ""

    for character in text:
        candidate = current + character
        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = character

    if current:
        parts.append(current)

    return parts


def _split_long_sentence(sentence: str, max_bytes: int):
    words = sentence.split()
    current = ""

    for word in words:
        candidate = (current + " " + word).strip() if current else word
        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
        else:
            if current:
                yield current
            if len(word.encode("utf-8")) <= max_bytes:
                current = word
            else:
                yield from _split_by_bytes(word, max_bytes)
                current = ""

    if current:
        yield current


def _iter_blocks(text: str) -> Iterable[str]:
    for block in re.split(r"(?:\r?\n\s*){2,}", text):
        candidate = block.strip()
        if candidate:
            yield candidate


def chunk_text(text: str, max_bytes: int) -> List[str]:
    """Split text into UTF-8 byte-aware chunks under the given limit."""
    if not text:
        return []

    chunks: List[str] = []
    current_block_accumulator = ""

    for block in _iter_blocks(text):
        block_bytes = len(block.encode("utf-8"))
        
        # If the block itself is small enough, try to add it to the current block accumulator
        if block_bytes <= max_bytes:
            candidate_block_accumulator = (f"{current_block_accumulator}\n\n{block}").strip() if current_block_accumulator else block
            if len(candidate_block_accumulator.encode("utf-8")) <= max_bytes:
                current_block_accumulator = candidate_block_accumulator
                continue # Continue to next block
            else:
                # Current block accumulator is full, append it and start new with current block
                if current_block_accumulator:
                    chunks.append(current_block_accumulator)
                current_block_accumulator = block
                continue

        # If block is too large, or current_block_accumulator is full, process sentences
        if current_block_accumulator: # Append any accumulated content before processing sentences
            chunks.append(current_block_accumulator)
            current_block_accumulator = ""

        sentences = re.split(r"(?<=[.!?])\s+", block)
        current_sentence_accumulator = "" # Accumulates content for the current sentence chunk

        for sentence in sentences:
            if not sentence.strip():
                continue
            
            stripped_sentence = sentence.strip()

            # If sentence itself is too long, split it
            if len(stripped_sentence.encode('utf-8')) > max_bytes:
                if current_sentence_accumulator: # Append any accumulated sentences
                    chunks.append(current_sentence_accumulator)
                    current_sentence_accumulator = ""
                chunks.extend(_split_long_sentence(stripped_sentence, max_bytes))
                continue

            # Try to add to current sentence chunk
            candidate_sentence_chunk = (current_sentence_accumulator + " " + stripped_sentence).strip()
            if len(candidate_sentence_chunk.encode('utf-8')) <= max_bytes:
                current_sentence_accumulator = candidate_sentence_chunk
            else:
                # Current sentence chunk is full, append it and start new with current sentence
                if current_sentence_accumulator:
                    chunks.append(current_sentence_accumulator)
                current_sentence_accumulator = stripped_sentence
        
        if current_sentence_accumulator: # Append any remaining sentence chunk
            chunks.append(current_sentence_accumulator)

    if current_block_accumulator: # Append any remaining block chunk
        chunks.append(current_block_accumulator)

    return [chunk.strip() for chunk in chunks if chunk.strip()]


FragmentBuilder = Callable[[str, bool], str]


def _fragment_size(fragment: str) -> int:
    return len(fragment.encode("utf-8", errors="ignore"))


def text_to_ssml_fragments(
    text: str,
    fragment_builder: FragmentBuilder,
    *,
    break_after: bool = False,
    max_bytes: Optional[int] = None,
) -> List[str]:
    """Convert plain text into SSML fragments that respect the API byte limit."""
    if not text or not text.strip():
        return []

    target_bytes = max_bytes or MAX_TTS_CHUNK_BYTES
    min_bytes = max(128, min(target_bytes, TTS_MIN_CHUNK_BYTES))

    while True:
        raw_chunks = chunk_text(text, target_bytes) or []
        fragments: List[str] = []
        oversize_bytes = 0

        for index, chunk in enumerate(raw_chunks):
            if not chunk.strip():
                continue

            fragment = fragment_builder(
                chunk,
                break_after=break_after and index == len(raw_chunks) - 1,
            )
            fragment_size = _fragment_size(fragment)
            if fragment_size > GOOGLE_TTS_MAX_INPUT_BYTES:
                oversize_bytes = fragment_size
                break
            fragments.append(fragment)

        if not oversize_bytes:
            return fragments

        if target_bytes <= min_bytes:
            raise SSMLChunkingError(
                "Generated SSML fragment exceeds the Google Text-to-Speech byte limit even after aggressive chunking."
            )

        prior_target = target_bytes
        step = max(128, target_bytes // 5)
        target_bytes = max(min_bytes, target_bytes - step)
        logger.warning(
            "Reducing TTS chunk size from %s to %s after SSML expansion hit %s bytes",
            prior_target,
            target_bytes,
            oversize_bytes,
        )
