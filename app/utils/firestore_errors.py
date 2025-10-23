"""
This module provides a decorator for handling common Firestore exceptions.
"""

import functools
import logging
from typing import TYPE_CHECKING, Type

from flask import abort, request
from google.api_core.exceptions import FailedPrecondition, GoogleAPICallError

from app.services.firestore_client import FirestoreError
from app.services.firestore_helpers import extract_index_url

if TYPE_CHECKING:  # pragma: no cover - import-time guard
    from app.services.firestore_client import FirestoreError  # noqa: F401

logger = logging.getLogger(__name__)


def _get_firestore_error() -> Type["FirestoreError"]:
    return FirestoreError


def handle_firestore_errors(func):
    """A decorator to handle common Firestore exceptions."""

    error_cls: Type["FirestoreError"] | None = None

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        nonlocal error_cls
        try:
            return func(*args, **kwargs)
        except FailedPrecondition as e:
            logger.warning(
                "firestore.index.missing",
                extra={"url": request.path, "hint": extract_index_url(e)},
            )
            abort(
                500,
                description="Firestore index missing — please create required index.",
            )
        except GoogleAPICallError as e:
            if error_cls is None:
                error_cls = _get_firestore_error()
            logger.error(f"Firestore error in {func.__name__}: {e}", exc_info=True)
            raise error_cls(f"A Firestore error occurred in {func.__name__}.") from e
        except Exception as e:
            if error_cls is None:
                error_cls = _get_firestore_error()
            logger.error(f"Unexpected error in {func.__name__}: {e}", exc_info=True)
            raise error_cls(f"An unexpected error occurred in {func.__name__}.") from e

    return wrapper
