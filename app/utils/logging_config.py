import logging
import logging.handlers
import os
import sys
import structlog
from typing import Any, Dict

try:
    from opentelemetry import trace  # type: ignore
except ImportError:  # pragma: no cover - otel optional in some environments
    trace = None  # type: ignore


_LOGGING_INITIALISED = False


REQUIRED_EVENT_FIELDS = (
    "event",
    "correlation_id",
    "task_id",
    "url",
    "status",
    "elapsed_ms",
)


def _inject_event_defaults(
    _: logging.Logger, __: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """Ensure core structured logging fields are present on every event."""
    context = structlog.contextvars.get_contextvars()

    for key in ("correlation_id", "task_id", "url", "path", "status_code"):
        if key not in event_dict and key in context:
            event_dict[key] = context[key]

    # Copy status/elapsed_ms from context when absent
    if "status" not in event_dict and "status" in context:
        event_dict["status"] = context["status"]
    if "elapsed_ms" not in event_dict and "elapsed_ms" in context:
        event_dict["elapsed_ms"] = context["elapsed_ms"]

    if "event" not in event_dict:
        # Fall back to message if present, otherwise use logger name.
        message = event_dict.get("message")
        event_dict["event"] = message or event_dict.get("logger", "log.event")

    for field in REQUIRED_EVENT_FIELDS:
        event_dict.setdefault(field, None)

    return event_dict


def _filter_noisy_events(
    logger: logging.Logger, name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any] | None:
    """
    Filters out noisy log events (static files, 304s) and downgrades
    http.request/response INFO to DEBUG, and filters non-essential requests.
    """
    event = event_dict.get("event")
    path = event_dict.get("path")
    status_code = event_dict.get("status_code")
    level = event_dict.get("level")

    # 1. Filter out static file requests
    if path and path.startswith("/static/"):
        return None

    # 2. Filter out 304 Not Modified responses
    if status_code == 304:
        return None

    # 3. Downgrade http.request and http.response from INFO to DEBUG
    if event in {"http.request", "http.response"} and level == "info":
        event_dict["level"] = "debug"
        # If we're downgrading, we might also want to filter non-essential ones
        # This check should happen after the downgrade, so we don't accidentally
        # filter out essential requests that were originally INFO.
        essential_paths = ["/", "/admin", "/tasks", "/feeds", "/items", "/auth"]
        is_essential = False
        if path:
            for ep in essential_paths:
                if path.startswith(ep):
                    is_essential = True
                    break
        if not is_essential:
            return None

    return event_dict


def _build_pre_chain(log_format: str):
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        _inject_event_defaults,
        _filter_noisy_events,  # Add the new filter here
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if log_format == "plain":
        # For plain formatting we still want human-friendly rendering.
        processors.append(structlog.processors.UnicodeDecoder())

    return processors


def _build_renderer(log_format: str):
    if log_format == "plain":
        return structlog.dev.ConsoleRenderer(colors=True)
    return structlog.processors.JSONRenderer()


def setup_logging(force: bool = False):
    global _LOGGING_INITIALISED
    if _LOGGING_INITIALISED and not force:
        return

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv("LOG_FORMAT", "json").strip().lower()
    if log_format not in {"json", "plain"}:
        log_format = "json"

    pre_chain = _build_pre_chain(log_format)
    renderer = _build_renderer(log_format)

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer, foreign_pre_chain=pre_chain, fmt="%(message)s"
    )

    structlog.configure(
        processors=[
            *pre_chain,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if os.getenv("ENV") == "development":
        log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "instance")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "development.log")

        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1024 * 1024 * 5, backupCount=5  # 5 MB per file
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        logging.info(f"Development log file enabled at: {log_file}")

    logging.getLogger("werkzeug").setLevel(logging.INFO)
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    _LOGGING_INITIALISED = True
