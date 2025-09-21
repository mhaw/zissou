import hashlib
import io
import json
import logging
import os
import re
import time
import html
import tempfile
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from datetime import datetime

from dateutil import parser as date_parser
from flask import Blueprint, jsonify, g, request
from pydub import AudioSegment  # type: ignore[import-untyped]

from app.models.item import Item
from app.services import buckets as buckets_service
from app.services import (
    items as items_service,
    buckets as buckets_service,
    parser,
    storage,
    tts,
    tasks as tasks_service,
    smart_buckets as smart_buckets_service,
)
from app.services.items import FirestoreError
from app.services.storage import StorageError
from app.services.tts import TTSError, get_audio_format_info
from app.services.ssml_chunker import (
    MAX_TTS_CHUNK_BYTES,
    SSMLChunkingError,
    chunk_text,
    text_to_ssml_fragments,
)

# Imports for token verification
from google.auth.transport import requests
from google.oauth2 import id_token

bp = Blueprint("tasks", __name__, url_prefix="/tasks")
logger = logging.getLogger(__name__)

NORMALIZE_AUDIO = os.getenv("TTS_NORMALIZE_AUDIO", "true").lower() not in {
    "false",
    "0",
    "no",
}

try:
    NORMALIZE_TARGET_DBFS = float(os.getenv("TTS_NORMALIZE_TARGET_DBFS", "-14"))
except (TypeError, ValueError):
    NORMALIZE_TARGET_DBFS = -14.0


def _normalize_audio_segment(segment: AudioSegment) -> AudioSegment:
    if not NORMALIZE_AUDIO:
        return segment
    if segment.dBFS == float("-inf"):
        return segment
    gain = NORMALIZE_TARGET_DBFS - segment.dBFS
    if abs(gain) < 1e-6:
        return segment
    return segment.apply_gain(gain)


class TaskErrorCodes:
    PARSER = "PARSER_ERROR"
    TTS = "TTS_ERROR"
    STORAGE = "STORAGE_ERROR"
    DATASTORE = "DATASTORE_ERROR"
    INVALID_INPUT = "INVALID_INPUT"
    UNKNOWN = "UNKNOWN_ERROR"


class ParsingError(Exception):
    """Raised when article parsing fails."""


def _verify_token(req):
    """Verify that the request is from Cloud Tasks."""
    auth_header = req.headers.get("Authorization")
    if not auth_header or "Bearer " not in auth_header:
        logger.warning("Task handler called without Authorization header.")
        return None, "Authorization header missing"

    token = auth_header.split("Bearer ")[1]

    try:
        audience = os.getenv("SERVICE_URL")
        if not audience:
            logger.error("SERVICE_URL environment variable not set.")
            return None, "Configuration error: SERVICE_URL not set"

        additional_audience = os.getenv("SERVICE_TASKS_URL")
        if not additional_audience and audience:
            additional_audience = audience.rstrip("/") + "/tasks/process"

        decoded_token = id_token.verify_oauth2_token(token, requests.Request())
        token_audience = decoded_token.get("aud")

        expected_audiences = {audience.rstrip("/")}
        if additional_audience:
            expected_audiences.add(additional_audience.rstrip("/"))

        def _normalize_audience(value):
            if isinstance(value, str):
                return value.rstrip("/")
            return value

        if isinstance(token_audience, (list, tuple, set)):
            audience_valid = any(
                _normalize_audience(aud) in expected_audiences for aud in token_audience
            )
        else:
            audience_valid = _normalize_audience(token_audience) in expected_audiences

        if not audience_valid:
            logger.error("Invalid token audience: %s", token_audience)
            return None, "Invalid token audience"

        invoker_email = os.getenv("SERVICE_ACCOUNT_EMAIL")
        if not invoker_email:
            logger.error("SERVICE_ACCOUNT_EMAIL environment variable not set.")
            return None, "Configuration error: SERVICE_ACCOUNT_EMAIL not set"

        if decoded_token.get("email") != invoker_email:
            logger.error("Invalid invoker email: %s", decoded_token.get("email"))
            return None, "Invalid invoker"

        return decoded_token, None
    except ValueError as exc:
        logger.exception("Token verification failed: %s", exc)
        return None, f"Token verification failed: {exc}"


def _build_narration_intro(parsed_data: dict, fallback_url: str, published_at) -> str:
    title = (parsed_data.get("title") or "Untitled").strip()
    author = (parsed_data.get("author") or "").strip()
    source_url = parsed_data.get("source_url") or fallback_url
    host = ""
    if source_url:
        host = urlparse(source_url).netloc.replace("www.", "") or source_url
    if not host and source_url:
        host = source_url

    if published_at:
        try:
            published_phrase = published_at.strftime("%B %d, %Y").replace(" 0", " ")
        except Exception:
            published_phrase = None
    else:
        published_phrase = None

    phrases: list[str] = [f'Today\'s feature is "{title}".']

    if host:
        phrases.append(f"It comes to us from {host}.")

    if author and author.lower() not in {"unknown", "n/a"}:
        phrases.append(f"Written by {author}.")

    if published_phrase:
        phrases.append(f"Originally published on {published_phrase}.")

    phrases.append("Take a moment to get comfortable—here is the article.")

    return " ".join(phrases)


SSML_PRONUNCIATIONS = [
    (re.compile(r"\bRSS\b", re.IGNORECASE), "R S S"),
    (re.compile(r"\bSaaS\b", re.IGNORECASE), "sass"),
    (re.compile(r"\bAI\b", re.IGNORECASE), "A I"),
    (re.compile(r"\bHTTP\b", re.IGNORECASE), "H T T P"),
]


def _ssml_escape(text: str) -> str:
    return html.escape(text, quote=False)


def _apply_pronunciations(text: str) -> str:
    placeholders: dict[str, tuple[str, str]] = {}

    def _substitute(pattern, alias, working_text):
        def replacer(match):
            placeholder = f"@@SUB{len(placeholders)}@@"
            placeholders[placeholder] = (match.group(0), alias)
            return placeholder

        return pattern.sub(replacer, working_text)

    working = text
    for pattern, alias in SSML_PRONUNCIATIONS:
        working = _substitute(pattern, alias, working)

    escaped = _ssml_escape(working)
    escaped = escaped.replace("\\r", " ")
    escaped = escaped.replace("\n\n", ' <break strength="medium"/> ')
    escaped = escaped.replace("\n", " ")

    for placeholder, (original, alias) in placeholders.items():
        escaped = escaped.replace(
            placeholder,
            f'<sub alias="{_ssml_escape(alias)}">{_ssml_escape(original)}</sub>',
        )

    return " ".join(escaped.split())


def _build_ssml_fragment(text: str, *, break_after: bool = False) -> str:
    processed = _apply_pronunciations(text)
    speak = ET.Element("speak")
    wrapper_markup = f"<wrapper>{processed}</wrapper>"
    try:
        wrapper_element = ET.fromstring(wrapper_markup)
    except ET.ParseError:
        speak.text = processed
    else:
        speak.text = wrapper_element.text
        for child in list(wrapper_element):
            speak.append(child)
    if break_after:
        ET.SubElement(speak, "break", {"time": "500ms"})
    return ET.tostring(speak, encoding="unicode")


@bp.route("/process", methods=["POST"])
def process_task_handler():
    """The webhook handler for Google Cloud Tasks."""

    _, error_message = _verify_token(request)
    if error_message:
        return (
            jsonify({"status": "error", "message": f"Unauthorized: {error_message}"}),
            403,
        )

    try:
        payload_str = request.get_data(as_text=True)
        payload = json.loads(payload_str)

        task_id = payload.get("task_id")
        g.task_id = task_id
        url = payload.get("url")
        voice = payload.get("voice")
        bucket_id = payload.get("bucket_id")

        if not task_id or not url:
            return (
                jsonify(
                    {"status": "error", "message": "Missing task_id or url in payload"}
                ),
                400,
            )

        claim_status, task = tasks_service.claim_task_for_processing(task_id)
        if claim_status == "missing":
            logger.error("Task %s received but not found in Firestore.", task_id)
            return jsonify({"status": "error", "message": "Task not found in DB"}), 200
        if claim_status == "duplicate":
            existing_status = task.status if task else "unknown"
            logger.warning(
                "Task %s received with status '%s'. Duplicate delivery acknowledged.",
                task_id,
                existing_status,
            )
            return (
                jsonify(
                    {"status": "acknowledged", "message": "Duplicate task ignored"}
                ),
                200,
            )

        if task:
            voice = voice or task.voice
            bucket_id = bucket_id or task.bucket_id
            user_id = user_id or task.userId # Use task.userId if not in payload

        process_article_task(task_id, url, voice, bucket_id, user_id)

        return jsonify({"status": "ok"}), 200

    except json.JSONDecodeError:
        logger.error("Task handler received invalid JSON.")
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400
    except Exception as exc:
        logger.exception("Unexpected error in task handler: %s", exc)
        return (
            jsonify({"status": "error", "message": "An unexpected error occurred"}),
            500,
        )


def process_article_task(task_id, url, voice=None, bucket_id=None, user_id=None):
    """The background task for processing an article."""
    logger.info("Task %s: Starting processing for URL: %s", task_id, url)
    start_time = datetime.utcnow()

    parsing_ms = 0
    tts_ms = 0
    upload_ms = 0
    chunk_count = 0

    try:
        if bucket_id:
            tasks_service.update_task(task_id, status="VALIDATING_INPUT")
            bucket = buckets_service.get_bucket(bucket_id)
            if not bucket:
                logger.warning(
                    "Task %s: Bucket %s missing. Continuing without bucket assignment.",
                    task_id,
                    bucket_id,
                )
                bucket_id = None
            else:
                bucket_id = getattr(bucket, "id", bucket_id)

        tasks_service.update_task(task_id, status="CHECKING_EXISTING")
        existing_item = items_service.find_item_by_source_url(url)
        if existing_item:
            tasks_service.update_task(
                task_id, status="COMPLETED", item_id=existing_item.id
            )
            logger.info(
                "Task %s: URL already processed as item %s", task_id, existing_item.id
            )
            return

        tasks_service.update_task(task_id, status="PARSING")
        parse_start = time.perf_counter()
        parsed_data = parser.extract_text(url)
        parsing_ms = int((time.perf_counter() - parse_start) * 1000)

        if parsed_data.get("error"):
            raise ParsingError(parsed_data["error"])

        if not parsed_data or not parsed_data.get("text"):
            raise ParsingError(
                "An unknown parsing error occurred. The extracted text was empty."
            )

        raw_text_content = parsed_data["text"] or ""
        text_content = raw_text_content.replace("\\r\\n", "\\n").replace("\\r", "\\n")
        parser_name = parsed_data.get("parser", "unknown")

        published_at = None
        if parsed_data.get("published_date"):
            try:
                published_at = date_parser.parse(parsed_data["published_date"])
            except (ValueError, TypeError):
                logger.warning(
                    "Task %s: Could not parse published_date: %s",
                    task_id,
                    parsed_data["published_date"],
                )

        narration_intro = _build_narration_intro(parsed_data, url, published_at)

        body_chunks = chunk_text(text_content, MAX_TTS_CHUNK_BYTES)
        text_length = len(narration_intro) + sum(len(chunk) for chunk in body_chunks)

        try:
            ssml_fragments = text_to_ssml_fragments(
                narration_intro, _build_ssml_fragment, break_after=True
            )
            for chunk in body_chunks:
                if chunk.strip():
                    ssml_fragments.extend(
                        text_to_ssml_fragments(chunk, _build_ssml_fragment)
                    )
        except SSMLChunkingError as exc:
            raise TTSError(str(exc)) from exc

        chunk_count = len(ssml_fragments)
        if chunk_count == 0:
            raise TTSError(
                "Generated narration script contained no synthesizable text."
            )

        tasks_service.update_task(task_id, status="CONVERTING_AUDIO")
        tts_start = time.perf_counter()

        format_info = get_audio_format_info()
        temp_files = []
        voice_setting = ""

        try:
            total_chunks = len(ssml_fragments)
            for index, fragment in enumerate(ssml_fragments, start=1):
                if index == 1 or index == total_chunks or index % 5 == 0:
                    logger.info(
                        "Task %s: Synthesizing chunk %s/%s",
                        task_id,
                        index,
                        total_chunks,
                    )
                else:
                    logger.debug(
                        "Task %s: Synthesizing chunk %s/%s",
                        task_id,
                        index,
                        total_chunks,
                    )
                audio_chunk_content, _, voice_setting = tts.text_to_speech(
                    fragment, voice_name=voice, use_ssml=True
                )

                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=f".{format_info['extension']}"
                ) as temp_f:
                    temp_f.write(audio_chunk_content)
                    temp_files.append(temp_f.name)
            combined_audio = AudioSegment.empty()
            for temp_f_name in temp_files:
                audio_segment = AudioSegment.from_file(
                    temp_f_name, format=format_info["pydub_format"]
                )
                combined_audio += audio_segment

            combined_audio = _normalize_audio_segment(combined_audio)

            final_audio_content_io = io.BytesIO()
            combined_audio.export(
                final_audio_content_io, format=format_info["pydub_format"]
            )
            audio_content = final_audio_content_io.getvalue()
            duration_seconds = len(combined_audio) / 1000.0

        finally:
            for temp_f_name in temp_files:
                try:
                    os.remove(temp_f_name)
                except OSError as e:
                    logger.error(f"Error deleting temp file {temp_f_name}: {e}")

        tts_ms = int((time.perf_counter() - tts_start) * 1000)

        tasks_service.update_task(task_id, status="UPLOADING_AUDIO")
        upload_start = time.perf_counter()
        audio_hash = hashlib.sha1(audio_content).hexdigest()
        blob_name = f"audio/{audio_hash}.{format_info['extension']}"
        audio_url = storage.upload_to_gcs(
            audio_content, blob_name, content_type=format_info["content_type"]
        )
        upload_ms = int((time.perf_counter() - upload_start) * 1000)

        tasks_service.update_task(task_id, status="SAVING_ITEM")

        processing_time_ms = int(
            (datetime.utcnow() - start_time).total_seconds() * 1000
        )
        pipeline_tools = list(
            dict.fromkeys([parser_name, "google-tts", "gcs", "pydub"])
        )

        new_item = Item(
            title=parsed_data.get("title", "Untitled"),
            sourceUrl=url,
            author=parsed_data.get("author", "Unknown"),
            text=text_content,
            audioUrl=audio_url,
            audioSizeBytes=len(audio_content),
            durationSeconds=duration_seconds,
            publishedAt=published_at,
            imageUrl=parsed_data.get("image_url"),
            processingTimeMs=processing_time_ms,
            voiceSetting=voice_setting,
            pipelineTools=pipeline_tools,
            parsingTimeMs=parsing_ms,
            ttsTimeMs=tts_ms,
            uploadTimeMs=upload_ms,
            chunkCount=chunk_count,
            textLength=text_length,
        )
        if bucket_id:
            new_item.buckets.append(bucket_id)
        item_id = items_service.create_item(new_item, user_id)

        # Apply smart buckets
        smart_buckets = smart_buckets_service.list_smart_buckets()
        for smart_bucket in smart_buckets:
            if smart_buckets_service.evaluate_item(new_item, smart_bucket.rules):
                if smart_bucket.id not in new_item.buckets:
                    new_item.buckets.append(smart_bucket.id)
        
        if new_item.buckets:
            items_service.update_item_buckets(item_id, new_item.buckets)

        tasks_service.update_task(task_id, status="COMPLETED", item_id=item_id)
        logger.info("Task %s: Completed successfully. Item ID: %s", task_id, item_id)

    except ParsingError as exc:
        error_message = f"Processing failed: {exc}"
        logger.exception("Task %s: %s", task_id, error_message)
        tasks_service.update_task(
            task_id,
            status="FAILED",
            error=error_message,
            error_code=TaskErrorCodes.PARSER,
        )
        raise
    except TTSError as exc:
        error_message = f"Processing failed: {exc}"
        logger.exception("Task %s: %s", task_id, error_message)
        tasks_service.update_task(
            task_id, status="FAILED", error=error_message, error_code=TaskErrorCodes.TTS
        )
        raise
    except StorageError as exc:
        error_message = f"Processing failed: {exc}"
        logger.exception("Task %s: %s", task_id, error_message)
        tasks_service.update_task(
            task_id,
            status="FAILED",
            error=error_message,
            error_code=TaskErrorCodes.STORAGE,
        )
        raise
    except FirestoreError as exc:
        error_message = f"Processing failed: {exc}"
        logger.exception("Task %s: %s", task_id, error_message)
        tasks_service.update_task(
            task_id,
            status="FAILED",
            error=error_message,
            error_code=TaskErrorCodes.DATASTORE,
        )
        raise
    except Exception as exc:
        error_message = f"An unexpected error occurred: {exc}"
        logger.exception("Task %s: %s", task_id, error_message)
        tasks_service.update_task(
            task_id,
            status="FAILED",
            error=error_message,
            error_code=TaskErrorCodes.UNKNOWN,
        )
        raise
