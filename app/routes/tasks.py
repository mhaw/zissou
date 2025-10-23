import hashlib
import html
import io
import json
import structlog
import os
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlparse

import jwt
import requests
from dateutil import parser as date_parser
from flask import Blueprint, jsonify, g, request
from pydub import AudioSegment  # type: ignore[import-untyped]
from typing import Any

from app.extensions import csrf
from app.models.item import Item
from app.services import buckets as buckets_service
from app.services import (
    items as items_service,
    parser,
    storage,
    tts,
    tasks as tasks_service,
    smart_buckets as smart_buckets_service,
)
from app.services import ai_enrichment
from app.services.firestore_client import FirestoreError
from app.services.storage import StorageError
from app.services.tts import TTSError, get_audio_format_info
from app.services.ssml_chunker import (
    MAX_TTS_CHUNK_BYTES,
    SSMLChunkingError,
    text_to_ssml_fragments,
)
from app.utils.correlation import (
    bind_request_context,
    bind_task_context,
    ensure_correlation_id,
    update_context,
)

bp = Blueprint("tasks", __name__, url_prefix="/tasks")
logger = structlog.get_logger(__name__)

NORMALIZE_AUDIO = os.getenv("TTS_NORMALIZE_AUDIO", "true").lower() not in {
    "false",
    "0",
    "no",
}

try:
    NORMALIZE_TARGET_DBFS = float(os.getenv("TTS_NORMALIZE_TARGET_DBFS", "-14"))
except (TypeError, ValueError):
    NORMALIZE_TARGET_DBFS = -14.0

_CERT_CACHE_TTL = int(os.getenv("TASK_CERT_CACHE_SECONDS", "3600"))
_cert_cache: dict[str, Any] = {}
_cert_cache_at: float | None = None
_cert_lock = threading.Lock()


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


def _bucket_is_public(bucket) -> bool:
    return bool(getattr(bucket, "is_public", False) or getattr(bucket, "public", False))


def _verify_headers(req):
    """Validate Cloud Tasks headers to guard against spoofed requests."""
    required_headers = (
        "X-CloudTasks-QueueName",
        "X-CloudTasks-TaskName",
        "X-CloudTasks-TaskRetryCount",
    )
    missing = [header for header in required_headers if header not in req.headers]
    if missing:
        logger.error("tasks.headers.missing", missing=missing)
        return None, f"Missing Cloud Tasks headers: {', '.join(missing)}"

    queue_header = req.headers.get("X-CloudTasks-QueueName", "")
    expected_queue = os.getenv("CLOUD_TASKS_QUEUE") or os.getenv("QUEUE")
    if expected_queue:
        expected_suffix = f"/queues/{expected_queue}".lower()
        normalized = queue_header.lower()
        if not (
            normalized.endswith(expected_suffix) or normalized == expected_queue.lower()
        ):
            logger.error(
                "tasks.queue_mismatch",
                expected_suffix=expected_suffix,
                received=queue_header,
            )
            return None, "Unexpected Cloud Tasks queue"

    return True, None


def _verify_token(req):
    """Verify that the request is from Cloud Tasks."""
    auth_header = req.headers.get("Authorization")
    if not auth_header or "Bearer " not in auth_header:
        logger.warning("tasks.auth.missing_header")
        return None, "Authorization header missing"

    token = auth_header.split("Bearer ")[1]

    try:
        public_keys = _get_google_public_keys()

        # 2. Decode the token header to get the key ID (kid)
        header = jwt.get_unverified_header(token)
        kid = header["kid"]
        public_key = public_keys.get(kid)
        if not public_key:
            logger.error("tasks.auth.public_key_missing", kid=kid)
            return None, "Invalid token: public key not found"

        # 3. Verify the token's signature and decode it
        decoded_token = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=None,  # We will manually verify audience
            issuer="https://accounts.google.com",
            options={"verify_aud": False},  # Disable audience verification by PyJWT
        )

        # 4. Manually verify audience
        service_url = os.getenv("SERVICE_URL")
        if not service_url:
            logger.error("tasks.auth.service_url_missing")
            return None, "Configuration error: SERVICE_URL not set"

        expected_audience = service_url.rstrip("/") + "/tasks/process"
        token_audience = decoded_token.get("aud")

        if token_audience != expected_audience:
            logger.error(
                "tasks.auth.audience_mismatch",
                expected=expected_audience,
                received=token_audience,
            )
            return None, "Invalid token audience"

        # 5. Manually verify issuer
        if decoded_token.get("iss") != "https://accounts.google.com":
            logger.error("tasks.auth.invalid_issuer", issuer=decoded_token.get("iss"))
            return None, "Invalid token issuer"

        # 6. Manually verify expiration
        if datetime.fromtimestamp(
            decoded_token.get("exp"), tz=timezone.utc
        ) < datetime.now(timezone.utc):
            logger.error("tasks.auth.token_expired")
            return None, "Token expired"

        invoker_email = os.getenv("SERVICE_ACCOUNT_EMAIL")
        if not invoker_email:
            logger.error("tasks.auth.service_account_missing")
            return None, "Configuration error: SERVICE_ACCOUNT_EMAIL not set"

        if decoded_token.get("email") != invoker_email:
            logger.error(
                "tasks.auth.invalid_invoker",
                invoker=decoded_token.get("email"),
                expected=invoker_email,
            )
            return None, "Invalid invoker"

        return decoded_token, None
    except jwt.exceptions.PyJWTError as exc:
        logger.exception("tasks.auth.token_verification_failed", error=str(exc))
        return None, f"Token verification failed: {exc}"
    except requests.exceptions.RequestException as exc:
        logger.exception("tasks.auth.public_key_fetch_failed", error=str(exc))
        return None, f"Failed to fetch public keys: {exc}"
    except Exception as exc:
        logger.exception("tasks.auth.unexpected_error", error=str(exc))
        return None, f"Unexpected error during token verification: {exc}"


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


@csrf.exempt
@bp.route("/process", methods=["POST"])
def process_task_handler():
    """The webhook handler for Google Cloud Tasks."""

    ensure_correlation_id(request.headers.get("X-Correlation-ID"))

    _, header_error = _verify_headers(request)
    if header_error:
        return (
            jsonify({"status": "error", "message": f"Unauthorized: {header_error}"}),
            403,
        )

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
        bucket_slug = payload.get("bucket_slug")
        if bucket_id:
            bucket_id, _ = tasks_service.normalize_bucket_reference(bucket_id)
        elif bucket_slug:
            bucket_id, _ = tasks_service.normalize_bucket_reference(bucket_slug)
        user_id = payload.get("user_id")

        if not task_id or not url:
            logger.error(
                "tasks.process.missing_payload_fields",
                payload_keys=list(payload.keys()),
            )
            return (
                jsonify(
                    {"status": "error", "message": "Missing task_id or url in payload"}
                ),
                400,
            )

        claim_status, task = tasks_service.claim_task_for_processing(task_id)
        if claim_status == "missing":
            bind_task_context(task_id=task_id)
            bind_request_context(url=url)
            request_logger = logger.bind(task_id=task_id, url=url)
            request_logger.error("tasks.process.missing_task")
            return jsonify({"status": "error", "message": "Task not found in DB"}), 200
        if claim_status == "duplicate":
            existing_status = task.status if task else "unknown"
            bind_task_context(task_id=task_id)
            bind_request_context(url=url)
            request_logger = logger.bind(task_id=task_id, url=url)
            request_logger.warning(
                "tasks.process.duplicate_delivery",
                existing_status=existing_status,
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
            user_id = user_id or task.userId  # Use task.userId if not in payload

        bind_task_context(task_id=task_id)
        bind_request_context(url=url)
        request_logger = logger.bind(task_id=task_id, url=url)
        request_logger.info(
            "tasks.process.accepted",
            queue=request.headers.get("X-CloudTasks-QueueName"),
        )

        process_article_task(task_id, url, voice, bucket_id, user_id)

        return jsonify({"status": "ok"}), 200

    except json.JSONDecodeError:
        logger.error("tasks.process.invalid_json")
        return jsonify({"status": "error", "message": "Invalid JSON payload"}), 400
    except Exception as exc:
        logger.exception("tasks.process.unexpected_error", error=str(exc))
        return (
            jsonify({"status": "error", "message": "An unexpected error occurred"}),
            500,
        )


def process_article_task(task_id, url, voice=None, bucket_id=None, user_id=None):
    """The background task for processing an article."""
    correlation_id = ensure_correlation_id()
    bind_task_context(task_id=task_id)
    bind_request_context(url=url)
    update_context(status="PROCESSING")

    task_logger = logger.bind(task_id=task_id, url=url)
    task_logger.info(
        "tasks.process.start",
        voice=voice,
        bucket_id=bucket_id,
        user_id=user_id,
    )

    start_time = datetime.now(timezone.utc)
    parsing_ms = 0
    tts_ms = 0
    upload_ms = 0
    chunk_count = 0

    def transition(status: str, **fields):
        tasks_service.update_task(task_id, status=status, **fields)
        update_context(status=status)
        task_logger.debug("tasks.status.transition", status=status, **fields)

    try:
        bucket_public = False
        if bucket_id:
            transition("VALIDATING_INPUT")
            bucket = buckets_service.get_bucket(bucket_id)
            if not bucket:
                task_logger.warning(
                    "tasks.bucket_missing",
                    bucket_id=bucket_id,
                )
                bucket_id = None
            else:
                bucket_id = getattr(bucket, "id", bucket_id)
                bucket_public = _bucket_is_public(bucket)
                task_logger.info("tasks.bucket_attached", bucket_id=bucket_id)

        transition("CHECKING_EXISTING")
        existing_item = items_service.find_item_by_source_url(url)
        if existing_item:
            transition("COMPLETED", item_id=existing_item.id)
            task_logger.info(
                "tasks.url_already_processed",
                item_id=existing_item.id,
            )
            return

        transition("PARSING")
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
        task_logger.info(
            "tasks.parse.succeeded",
            parser=parser_name,
            parsing_ms=parsing_ms,
        )

        published_at = None
        if parsed_data.get("published_date"):
            try:
                published_at = date_parser.parse(parsed_data["published_date"])
            except (ValueError, TypeError):
                task_logger.warning(
                    "tasks.published_date_parse_failed",
                    published_date=parsed_data["published_date"],
                )

        narration_intro = _build_narration_intro(parsed_data, url, published_at)

        total_text_bytes = len(
            (narration_intro + "\n\n" + text_content).encode("utf-8", errors="ignore")
        )

        try:
            ssml_fragments = text_to_ssml_fragments(
                narration_intro,
                _build_ssml_fragment,
                break_after=True,
                max_bytes=MAX_TTS_CHUNK_BYTES,
            )
            ssml_fragments.extend(
                text_to_ssml_fragments(
                    text_content,
                    _build_ssml_fragment,
                    max_bytes=MAX_TTS_CHUNK_BYTES,
                )
            )
        except SSMLChunkingError as exc:
            raise TTSError(str(exc)) from exc

        chunk_count = len(ssml_fragments)
        if chunk_count == 0:
            raise TTSError(
                "Generated narration script contained no synthesizable text."
            )

        transition("CONVERTING_AUDIO")
        tts_start = time.perf_counter()

        format_info = get_audio_format_info()
        temp_files = []
        voice_setting = ""

        try:
            total_chunks = len(ssml_fragments)
            for index, fragment in enumerate(ssml_fragments, start=1):
                if index in {1, total_chunks}:
                    task_logger.info(
                        "tts.chunk",
                        chunk_index=index,
                        chunk_total=total_chunks,
                    )
                elif index % 5 == 0:
                    task_logger.debug(
                        "tts.chunk",
                        chunk_index=index,
                        chunk_total=total_chunks,
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
                except OSError as exc:
                    task_logger.error(
                        "tasks.tempfile_cleanup_failed",
                        path=temp_f_name,
                        error=str(exc),
                    )

        tts_ms = int((time.perf_counter() - tts_start) * 1000)
        task_logger.info(
            "tts.completed",
            chunk_count=chunk_count,
            tts_ms=tts_ms,
            voice_setting=voice_setting,
        )

        transition("UPLOADING_AUDIO")
        upload_start = time.perf_counter()
        audio_hash = hashlib.sha1(audio_content).hexdigest()
        blob_name = f"audio/{audio_hash}.{format_info['extension']}"
        task_logger.info("storage.upload.start", blob_name=blob_name)
        audio_url = storage.upload_to_gcs(
            audio_content, blob_name, content_type=format_info["content_type"]
        )
        upload_ms = int((time.perf_counter() - upload_start) * 1000)

        transition("SAVING_ITEM")
        task_logger.info(
            "task.pipeline",
            parser=parser_name,
            chunk_count=chunk_count,
            text_bytes=total_text_bytes,
            parsing_ms=parsing_ms,
            tts_ms=tts_ms,
            upload_ms=upload_ms,
        )

        processing_time_ms = int(
            (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        )
        pipeline_tools = list(
            dict.fromkeys([parser_name, "google-tts", "gcs", "pydub"])
        )
        text_length = len(narration_intro) + len(text_content)

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
            is_public=bucket_public,
        )
        if bucket_id:
            new_item.buckets.append(bucket_id)
        item_id = items_service.create_item(new_item, user_id)

        article_text = f"{narration_intro}\n\n{text_content}".strip()
        ai_enrichment.maybe_schedule_enrichment(item_id, article_text, correlation_id)

        smart_buckets = smart_buckets_service.list_smart_buckets()
        for smart_bucket in smart_buckets:
            if smart_buckets_service.evaluate_item(new_item, smart_bucket.rules):
                if smart_bucket.id not in new_item.buckets:
                    new_item.buckets.append(smart_bucket.id)

        if new_item.buckets:
            items_service.update_item_buckets(item_id, new_item.buckets)

        transition("COMPLETED", item_id=item_id)
        task_logger.info(
            "tasks.process.completed",
            item_id=item_id,
            processing_ms=processing_time_ms,
            parsing_ms=parsing_ms,
            tts_ms=tts_ms,
            upload_ms=upload_ms,
            chunk_count=chunk_count,
        )

    except ParsingError as exc:
        error_message = f"Processing failed: {exc}"
        task_logger.exception(
            "tasks.process.failed",
            error_code=TaskErrorCodes.PARSER,
            error_message=error_message,
        )
        transition(
            "FAILED",
            error=error_message,
            error_code=TaskErrorCodes.PARSER,
        )
        raise
    except TTSError as exc:
        error_message = f"Processing failed: {exc}"
        task_logger.exception(
            "tasks.process.failed",
            error_code=TaskErrorCodes.TTS,
            error_message=error_message,
        )
        transition(
            "FAILED",
            error=error_message,
            error_code=TaskErrorCodes.TTS,
        )
        raise
    except StorageError as exc:
        error_message = f"Processing failed: {exc}"
        task_logger.exception(
            "tasks.process.failed",
            error_code=TaskErrorCodes.STORAGE,
            error_message=error_message,
        )
        transition(
            "FAILED",
            error=error_message,
            error_code=TaskErrorCodes.STORAGE,
        )
        raise
    except FirestoreError as exc:
        error_message = f"Processing failed: {exc}"
        task_logger.exception(
            "tasks.process.failed",
            error_code=TaskErrorCodes.DATASTORE,
            error_message=error_message,
        )
        transition(
            "FAILED",
            error=error_message,
            error_code=TaskErrorCodes.DATASTORE,
        )
        raise
    except Exception as exc:
        error_message = f"An unexpected error occurred: {exc}"
        task_logger.exception(
            "tasks.process.failed",
            error_code=TaskErrorCodes.UNKNOWN,
            error_message=error_message,
        )
        transition(
            "FAILED",
            error=error_message,
            error_code=TaskErrorCodes.UNKNOWN,
        )
        raise


def _get_google_public_keys() -> dict[str, Any]:
    global _cert_cache, _cert_cache_at

    now = time.monotonic()
    with _cert_lock:
        if _cert_cache and _cert_cache_at and now - _cert_cache_at < _CERT_CACHE_TTL:
            return _cert_cache

        response = requests.get("https://www.googleapis.com/oauth2/v3/certs", timeout=5)
        response.raise_for_status()
        keys = {
            jwk["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
            for jwk in response.json().get("keys", [])
        }
        _cert_cache = keys
        _cert_cache_at = now
        return _cert_cache
