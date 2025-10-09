import logging
import logging.handlers
import os
import sys
from flask import g
import structlog

try:
    from opentelemetry import trace  # type: ignore
except ImportError:  # pragma: no cover - otel optional in some environments
    trace = None  # type: ignore


def setup_logging():
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
        )
    )
    root_logger.addHandler(console_handler)

    if os.getenv("ENV") == "development":
        log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "instance")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "development.log")

        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1024 * 1024 * 5, backupCount=5  # 5 MB per file
        )
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=structlog.processors.JSONRenderer(),
            )
        )
        root_logger.addHandler(file_handler)
        logging.info(f"Development log file enabled at: {log_file}")

    logging.getLogger("werkzeug").setLevel(logging.INFO)
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
