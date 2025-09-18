import logging
import os
from flask import g

try:
    from pythonjsonlogger import jsonlogger
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    import json

    class _FallbackJsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload = {
                "timestamp": self.formatTime(record),
                "severity": record.levelname,
                "message": record.getMessage(),
                "filename": record.filename,
                "lineno": record.lineno,
                "trace_id": getattr(record, "trace_id", None),
                "task_id": getattr(record, "task_id", None),
            }
            return json.dumps(payload)

    class jsonlogger:  # type: ignore
        JsonFormatter = _FallbackJsonFormatter  # type: ignore[attr-defined]


try:
    from opentelemetry import trace  # type: ignore
except ImportError:  # pragma: no cover - otel optional in some environments
    trace = None  # type: ignore


class ContextualFilter(logging.Filter):
    """Injects contextual information into log records for tracing/task IDs."""

    def filter(self, record):
        record.trace_id = None
        record.trace = None
        record.task_id = None

        if trace:
            try:
                span = trace.get_current_span()
                span_context = span.get_span_context() if span else None
                trace_id = span_context.trace_id if span_context else 0
            except Exception:  # pragma: no cover - defensive fallback
                trace_id = 0

            if trace_id:
                record.trace_id = format(trace_id, "x")
                project_id = os.getenv("GCP_PROJECT_ID")
                if project_id:
                    record.trace = f"projects/{project_id}/traces/{record.trace_id}"

        try:
            task_id = g.get("task_id")  # type: ignore[attr-defined]
        except RuntimeError:
            task_id = None

        if task_id:
            record.task_id = task_id

        return True


def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    if logger.handlers:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)

    console_handler = logging.StreamHandler()
    console_handler.addFilter(ContextualFilter())

    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(message)s %(filename)s %(lineno)d "
        "(trace_id=%(trace_id)s) (task_id=%(task_id)s)",
        rename_fields={"levelname": "severity", "asctime": "timestamp"},
    )

    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
