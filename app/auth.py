"""Authentication helpers for Firebase-backed session cookies."""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from flask import abort, current_app, flash, g, redirect, request, session, url_for
from firebase_admin import auth as firebase_auth  # type: ignore[import-untyped]

TCallable = TypeVar("TCallable", bound=Callable[..., Any])
logger = logging.getLogger(__name__)


def _admin_email_set() -> set[str]:
    raw_value = current_app.config.get("ADMIN_EMAILS", [])
    if isinstance(raw_value, str):
        candidates = [piece.strip().lower() for piece in raw_value.split(",")]
    else:
        candidates = [str(piece).strip().lower() for piece in raw_value]
    return {candidate for candidate in candidates if candidate}


def build_user_context(claims: dict[str, Any]) -> dict[str, Any]:
    """Map Firebase claims into the session payload we persist locally."""
    email = (claims.get("email") or "").lower()
    admins = _admin_email_set()
    is_admin = not admins or (email and email in admins)
    role = "admin" if is_admin else "member"
    return {
        "uid": claims.get("uid"),
        "email": claims.get("email"),
        "name": claims.get("name"),
        "role": role,
    }


def _sync_session_user(user: Optional[dict[str, Any]]) -> None:
    if not user:
        if session.pop("user", None) is not None:
            session.modified = True
        return

    payload = {
        key: user.get(key) for key in ("uid", "email", "name", "role", "default_voice") if user.get(key)
    }
    if session.get("user") != payload:
        session["user"] = payload


def _verify_session_cookie() -> Optional[dict[str, Any]]:
    """Decode the Firebase session cookie if present and return user context."""
    session_cookie_name = current_app.config.get(
        "SESSION_COOKIE_NAME", "__zissou_session"
    )
    cookie = request.cookies.get(session_cookie_name)
    if not cookie:
        _sync_session_user(None)
        return None

    try:
        claims = firebase_auth.verify_session_cookie(cookie, check_revoked=True)
    except Exception:  # pylint: disable=broad-except
        logger.debug("Session cookie verification failed", exc_info=True)
        _sync_session_user(None)
        return None

    user_context = build_user_context(claims)
    _sync_session_user(user_context)
    return user_context


def ensure_user() -> Optional[dict[str, Any]]:
    """Ensure g.user is populated with the current authenticated user context."""
    user = getattr(g, "user", None)
    if user is not None:
        return user

    user = _verify_session_cookie()
    g.user = user
    return user


def require_roles(*roles: str):
    """Enforce authentication and optional role membership, returning a response if blocked."""
    if not current_app.config.get("AUTH_ENABLED", False):
        return None

    user = ensure_user()
    if not user:
        login_url = url_for("auth.login", next=request.path)
        return redirect(login_url, code=302)

    if roles:
        allowed = {role.lower() for role in roles if role}
        current_role = (user.get("role") or "").lower()
        if allowed and current_role not in allowed:
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

        return wrapper  # type: ignore[return-value]

    return decorator
