"""Runtime import validation helpers for early failure signalling."""

from __future__ import annotations

import importlib
from typing import Iterable

import structlog

DEFAULT_MODULES: tuple[str, ...] = (
    "app",
    "app.services.parser",
    "app.routes.tasks",
)

logger = structlog.get_logger(__name__)


def verify_imports(modules: Iterable[str] = DEFAULT_MODULES) -> None:
    """Import each module eagerly to surface errors during startup."""
    for module_path in modules:
        try:
            importlib.import_module(module_path)
            logger.info("startup.import_check", module=module_path, status="ok")
        except Exception as exc:  # pragma: no cover - defensive guardrail
            logger.error(
                "startup.import_check",
                module=module_path,
                status="failed",
                error=str(exc),
            )
            raise RuntimeError(f"Import failed for {module_path}: {exc}") from exc
