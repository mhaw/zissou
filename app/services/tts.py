import io
import logging
import os
import time
import re
from typing import Tuple

from google.api_core.exceptions import GoogleAPIError
from google.cloud import texttospeech
from pydub import AudioSegment
from pydub import exceptions as pydub_exceptions

logger = logging.getLogger(__name__)

_TRANSIENT_TTS_CODES = {
    "DEADLINE_EXCEEDED",
    "UNAVAILABLE",
    "ABORTED",
    "INTERNAL",
    "RESOURCE_EXHAUSTED",
}
_PERMANENT_TTS_CODES = {"INVALID_ARGUMENT", "FAILED_PRECONDITION", "PERMISSION_DENIED"}


def _classify_tts_error(exc: GoogleAPIError) -> str:
    """Return 'transient', 'permanent', or 'unknown' for a GoogleAPIError."""
    code = getattr(exc, "code", None)
    code_name = None
    if code is not None:
        code_name = getattr(code, "name", None) or str(code)
    if code_name:
        code_name = code_name.upper()
        if code_name in _PERMANENT_TTS_CODES:
            return "permanent"
        if code_name in _TRANSIENT_TTS_CODES:
            return "transient"
    message = str(exc).lower()
    if "invalid ssml" in message or "invalid argument" in message:
        return "permanent"
    if "audio content is empty" in message or "must be less than" in message:
        return "permanent"
    if "quota" in message or "exceeded" in message or "backend error" in message:
        return "transient"
    return "unknown"


AUDIO_ENCODING = os.getenv("TTS_AUDIO_ENCODING", "MP3")
SPEAKING_RATE = float(os.getenv("TTS_SPEAKING_RATE", 1.0))
MAX_ARTICLE_LENGTH_CHARS = int(os.getenv("MAX_ARTICLE_LENGTH_CHARS", 18000))
MAX_TTS_ATTEMPTS = int(os.getenv("TTS_MAX_ATTEMPTS", 3))
TTS_RETRY_INITIAL_BACKOFF = float(os.getenv("TTS_RETRY_INITIAL_BACKOFF", 0.5))

VOICE_PROFILES = {
    "captains-log": {
        "name": "en-US-Neural2-F",
        "gender": texttospeech.SsmlVoiceGender.FEMALE,
        "description": "Captain's Log (US English, Warm Narrative)",
        "speaking_rate": 0.98,
        "pitch": -2.0,
    },
    "deep-dive": {
        "name": "en-US-Studio-O",
        "gender": texttospeech.SsmlVoiceGender.FEMALE,
        "description": "Deep Dive (US Studio, Calm & Clear)",
        "speaking_rate": 0.95,
        "pitch": -1.0,
    },
    "first-mate": {
        "name": "en-GB-Neural2-B",
        "gender": texttospeech.SsmlVoiceGender.MALE,
        "description": "First Mate (UK English, Conversational)",
        "speaking_rate": 1.0,
        "pitch": -1.0,
    },
    "science-officer": {
        "name": "en-AU-Neural2-C",
        "gender": texttospeech.SsmlVoiceGender.FEMALE,
        "description": "Science Officer (AU English, Crisp)",
        "speaking_rate": 0.99,
        "pitch": 0.0,
    },
}

_AUDIO_FORMATS = {
    "MP3": {"pydub_format": "mp3", "extension": "mp3", "content_type": "audio/mpeg"},
    "OGG_OPUS": {
        "pydub_format": "ogg",
        "extension": "ogg",
        "content_type": "audio/ogg",
    },
    "LINEAR16": {
        "pydub_format": "wav",
        "extension": "wav",
        "content_type": "audio/wav",
    },
}

_tts_client: texttospeech.TextToSpeechClient | None = None


def get_audio_format_info() -> dict:
    """Return formatting information for the configured audio encoding."""
    info = _AUDIO_FORMATS.get(AUDIO_ENCODING.upper())
    if not info:
        logger.warning(
            "Unsupported AUDIO_ENCODING %s; defaulting to MP3", AUDIO_ENCODING
        )
        info = _AUDIO_FORMATS["MP3"]
    return info


def get_audio_encoding_key() -> str:
    encoding = AUDIO_ENCODING.upper()
    if encoding not in _AUDIO_FORMATS:
        logger.warning(
            "Unsupported AUDIO_ENCODING %s; defaulting to MP3", AUDIO_ENCODING
        )
        return "MP3"
    return encoding


def _get_tts_client() -> texttospeech.TextToSpeechClient:
    global _tts_client
    if _tts_client is None:
        _tts_client = texttospeech.TextToSpeechClient()
    return _tts_client


def text_to_speech(
    text: str, voice_name: str = "captains-log", use_ssml: bool = False
) -> Tuple[bytes, float, str]:
    """Converts text (or SSML) to speech using Google Text-to-Speech API."""
    if not text:
        raise TTSError("Text cannot be empty for TTS conversion.")

    length_check_payload = text
    if use_ssml:
        stripped = re.sub(r"<[^>]+>", "", text)
        length_check_payload = stripped
    if len(length_check_payload) > MAX_ARTICLE_LENGTH_CHARS:
        logger.warning(
            "Article length (%s) exceeds MAX_ARTICLE_LENGTH_CHARS (%s). Truncating.",
            len(length_check_payload),
            MAX_ARTICLE_LENGTH_CHARS,
        )
        if use_ssml:
            raise TTSError(
                "Cannot automatically truncate SSML input; consider shortening the source text."
            )
        text = text[:MAX_ARTICLE_LENGTH_CHARS]

    client = _get_tts_client()

    voice_profile = VOICE_PROFILES.get(voice_name, VOICE_PROFILES["captains-log"])
    voice_selection_params = texttospeech.VoiceSelectionParams(
        language_code="-".join(voice_profile["name"].split("-")[:2]),
        name=voice_profile["name"],
        ssml_gender=voice_profile["gender"],
    )

    speaking_rate = voice_profile.get("speaking_rate", SPEAKING_RATE)
    pitch = voice_profile.get("pitch")

    audio_config_kwargs = {
        "audio_encoding": texttospeech.AudioEncoding[get_audio_encoding_key()],
        "speaking_rate": speaking_rate,
    }
    if pitch is not None:
        audio_config_kwargs["pitch"] = pitch

    audio_config = texttospeech.AudioConfig(**audio_config_kwargs)

    if use_ssml:
        synthesis_input = texttospeech.SynthesisInput(ssml=text)
    else:
        synthesis_input = texttospeech.SynthesisInput(text=text)

    last_error: Exception | None = None
    for attempt in range(1, MAX_TTS_ATTEMPTS + 1):
        try:
            response = client.synthesize_speech(
                input=synthesis_input,
                voice=voice_selection_params,
                audio_config=audio_config,
            )
            break
        except GoogleAPIError as exc:
            last_error = exc
            classification = _classify_tts_error(exc)
            error_detail = str(exc)
            if classification == "permanent":
                logger.error(
                    "Permanent Text-to-Speech error on attempt %s/%s: %s",
                    attempt,
                    MAX_TTS_ATTEMPTS,
                    error_detail,
                )
                raise TTSError(
                    f"Google Text-to-Speech rejected the request: {error_detail}"
                ) from exc
            if attempt == MAX_TTS_ATTEMPTS:
                logger.error(
                    "Google Text-to-Speech API failed after %s attempts: %s",
                    attempt,
                    error_detail,
                )
                raise TTSError(
                    f"Google Text-to-Speech API error: {error_detail}"
                ) from exc
            sleep_for = TTS_RETRY_INITIAL_BACKOFF * (2 ** (attempt - 1))
            if classification == "transient":
                logger.warning(
                    "Transient Text-to-Speech error on attempt %s/%s: %s. Retrying in %.2fs",
                    attempt,
                    MAX_TTS_ATTEMPTS,
                    error_detail,
                    sleep_for,
                )
            else:
                logger.warning(
                    "Google Text-to-Speech API error on attempt %s/%s (treating as retryable): %s. Retrying in %.2fs",
                    attempt,
                    MAX_TTS_ATTEMPTS,
                    error_detail,
                    sleep_for,
                )
            time.sleep(sleep_for)
        except Exception as exc:
            last_error = exc
            logger.exception("Unexpected error during TTS synthesis")
            raise TTSError(f"Unexpected error during TTS synthesis: {exc}") from exc
    else:
        raise TTSError(f"Google Text-to-Speech API error: {last_error}")

    info = get_audio_format_info()

    try:
        audio_segment = AudioSegment.from_file(
            io.BytesIO(response.audio_content), format=info["pydub_format"]
        )
        duration_seconds = len(audio_segment) / 1000.0
    except pydub_exceptions.PydubError as exc:
        logger.error("Pydub error processing audio: %s", exc)
        duration_seconds = 0.0
    except Exception as exc:
        logger.error("Unexpected error calculating audio duration: %s", exc)
        duration_seconds = 0.0

    return response.audio_content, duration_seconds, voice_profile["description"]


class TTSError(Exception):
    """Custom exception for Text-to-Speech related errors."""

    pass
