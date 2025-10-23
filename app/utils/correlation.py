from __future__ import annotations

import structlog
from contextlib import suppress
from typing import Any, Optional
from uuid import uuid4

from flask import g


def _get_flask_context_id() -> Optional[str]:
    with suppress(RuntimeError):
        if g is not None and hasattr(g, "correlation_id"):
            return getattr(g, "correlation_id")
    return None


def current_correlation_id() -> Optional[str]:
    """Return the active correlation id if bound."""
    cid = _get_flask_context_id()
    if cid:
        return cid
    context = structlog.contextvars.get_contextvars()
    return context.get("correlation_id")


def ensure_correlation_id(value: Optional[str] = None) -> str:
    """Guarantee that a correlation id is bound and returned."""
    correlation_id = value or current_correlation_id() or uuid4().hex
    with suppress(RuntimeError):
        setattr(g, "correlation_id", correlation_id)
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    return correlation_id


def bind_task_context(task_id: Optional[str] = None, **extra: Any) -> None:
    """Bind task-related identifiers into the logging context."""
    context: dict[str, Any] = {"task_id": task_id}
    context.update(extra)
    structlog.contextvars.bind_contextvars(**context)


def bind_request_context(url: Optional[str] = None, **extra: Any) -> None:
    """Bind request metadata into the logging context."""
    context: dict[str, Any] = {"url": url}
    context.update(extra)
    structlog.contextvars.bind_contextvars(**context)


def update_context(**extra: Any) -> None:
    """Merge additional fields into the structured logging context."""
    if extra:
        structlog.contextvars.bind_contextvars(**extra)


def clear_correlation_context() -> None:
    """Reset correlation and related context vars for the current scope."""
    structlog.contextvars.clear_contextvars()
    with suppress(RuntimeError):
        if g is not None and hasattr(g, "correlation_id"):
            delattr(g, "correlation_id")
