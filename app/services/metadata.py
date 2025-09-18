import re


def get_audio_duration(audio_bytes: int, audio_encoding: str = "MP3") -> float:
    """Estimates audio duration in seconds. Very approximate."""
    # This is a rough estimation. For accurate duration, a library like
    # mutagen or ffprobe would be needed, which adds system dependencies.
    # For MP3, a common bitrate is 128kbps. 128,000 bits / 8 bits/byte = 16,000 bytes/sec.
    if audio_encoding == "MP3":
        bytes_per_second = 16000
    else:  # Default for other formats, less accurate
        bytes_per_second = 16000

    if not audio_bytes or bytes_per_second == 0:
        return 0.0

    return audio_bytes / bytes_per_second


def chunk_text(text: str, max_length: int) -> list[str]:
    """Splits text into chunks for TTS processing."""
    # Google TTS has a 5000 byte limit per request.
    # We chunk by paragraphs first, then by sentences, then by words to be safe.
    chunks = []

    # First, split by paragraphs
    paragraphs = text.split("\n\n")
    for para in paragraphs:
        if len(para.encode("utf-8")) <= max_length:
            if para.strip():
                chunks.append(para)
        else:
            # If paragraph is too long, split by sentences
            sentences = re.split(r"(?<=[.!?]) +(?=[A-Z])", para)
            current_chunk = ""
            for sentence in sentences:
                if len((current_chunk + sentence + " ").encode("utf-8")) <= max_length:
                    current_chunk += sentence + " "
                else:
                    chunks.append(current_chunk.strip())
                    current_chunk = sentence + " "
            if current_chunk.strip():
                chunks.append(current_chunk.strip())

    return chunks
