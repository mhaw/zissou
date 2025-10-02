"""Authentication helpers for Firebase-backed session cookies."""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from flask import abort, current_app, g, redirect, request, url_for
from firebase_admin import auth as firebase_auth  # type: ignore[import-untyped]

TCallable = TypeVar("TCallable", bound=Callable[..., Any])
logger = logging.getLogger(__name__)

from app.constants import FB_COOKIE

PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/static/",
    "/auth/",
    "/favicon.ico",
    "/robots.txt",
    "/sitemap.xml",
    "/manifest.json",
    "/.well-known/",
    "/healthz",
    "/status",
    "/ping",
)
PUBLIC_ENDPOINT_PREFIXES: tuple[str, ...] = ("auth.",)
PUBLIC_ENDPOINTS: set[str] = {"static"}


def _admin_email_set() -> set[str]:
    raw_value = current_app.config.get("ADMIN_EMAILS", [])
    if isinstance(raw_value, str):
        candidates = [piece.strip().lower() for piece in raw_value.split(",")]
    else:
        candidates = [str(piece).strip().lower() for piece in raw_value]
    return {candidate for candidate in candidates if candidate}


def build_user_context(
    claims: dict[str, Any], db_user: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """Map Firebase claims and a database user record into the session payload."""
    db_user = db_user or {}
    # Check for Multi-Factor Authentication
    auth_methods = claims.get("amr", [])
    is_mfa = "mfa" in auth_methods

    return {
        "uid": claims.get("uid"),
        "email": claims.get("email"),
        "name": claims.get("name"),
        "role": db_user.get("role", "member"),
        "is_mfa": is_mfa,
    }


def get_current_user_from_token() -> Optional[dict[str, Any]]:
    """Decode the Firebase ID token if present and return user context."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None

    try:
        id_token = auth_header.split(" ").pop()
        decoded = firebase_auth.verify_id_token(id_token, check_revoked=True)
        return build_user_context(decoded)
    except Exception as e:
        current_app.logger.info(f"verify_id_token failed: {e.__class__.__name__}: {e}")
        return None


def ensure_user() -> Optional[dict[str, Any]]:
    """Ensure g.user is populated with the current authenticated user context."""
    if not hasattr(g, "user"):
        g.user = get_current_user_from_token()
    return g.user


def require_roles(*roles: str):
    """Enforce authentication and optional role membership, returning a response if blocked."""
    if not current_app.config.get("AUTH_ENABLED", False):
        return None

    user = ensure_user()
    if not user:
        logger.info(
            "Unauthenticated access attempt blocked",
            extra={
                "auth_event": "auth_required_failure",
                "path": request.path,
                "ip": request.remote_addr,
            },
        )
        login_url = url_for("auth.login", next=request.full_path)
        return redirect(login_url, code=302)

    if roles:
        allowed = {role.lower() for role in roles if role}
        current_role = (user.get("role") or "").lower()
        if allowed and current_role not in allowed:
            logger.warning(
                "User role authorization failure",
                extra={
                    "auth_event": "role_required_failure",
                    "user_id": user.get("uid"),
                    "user_role": current_role,
                    "required_roles": list(allowed),
                    "path": request.path,
                    "ip": request.remote_addr,
                },
            )
            abort(403)

    return None


def auth_required(view: TCallable) -> TCallable:
    """Guard a route, redirecting to the login flow when auth is active."""

    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any):
        response = require_roles()
        if response is not None:
            return response
        return view(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def role_required(*roles: str):
    """Guard a route and ensure the authenticated user has one of the given roles."""

    def decorator(view: TCallable) -> TCallable:
        @wraps(view)
        def wrapper(*args: Any, **kwargs: Any):
            response = require_roles(*roles)
            if response is not None:
                return response
            return view(*args, **kwargs)

        return wrapper

    return decorator