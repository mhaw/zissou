"""Authentication helpers for Firebase or Google IAP backed sessions."""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from flask import abort, current_app, g, redirect, request, url_for
from firebase_admin import auth as firebase_auth  # type: ignore[import-untyped]

from app.constants import FB_COOKIE
from app.models.user import User
from app.services import users as users_service

TCallable = TypeVar("TCallable", bound=Callable[..., Any])
logger = logging.getLogger(__name__)

COOKIE_NAME = FB_COOKIE

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


def _get_auth_backend() -> str:
    backend = current_app.config.get("AUTH_BACKEND", "iap")
    if isinstance(backend, str):
        backend = backend.strip().lower()
    else:
        backend = "iap"
    if backend not in {"firebase", "iap"}:
        logger.warning("Unknown AUTH_BACKEND %s; defaulting to 'iap'.", backend)
        backend = "iap"
    return backend


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
    auth_methods = claims.get("amr", [])
    is_mfa = "mfa" in auth_methods or bool(db_user.get("is_mfa"))

    uid = claims.get("uid") or claims.get("sub")
    email = (claims.get("email") or "").lower()
    name = claims.get("name") or db_user.get("name") or email.split("@")[0]
    role = db_user.get("role", "member")

    return {
        "uid": uid,
        "email": email,
        "name": name,
        "role": role,
        "is_mfa": is_mfa,
    }


def _sync_user_record(uid: str, email: str, name: str, role: str) -> Optional[User]:
    """Ensure there is a backing user record in Firestore when possible."""
    db_client = getattr(users_service, "db", None)
    if db_client is None:
        return None

    try:
        existing = users_service.get_user(uid)
    except users_service.FirestoreError:
        logger.exception("Failed to load user record for %s", uid)
        return None

    if existing:
        return existing

    try:
        users_service.create_user(
            User(
                id=uid,
                email=email,
                name=name,
                role=role,
            )
        )
    except users_service.FirestoreError:
        logger.exception("Unable to create user record for %s", uid)
        return None

    try:
        return users_service.get_user(uid)
    except users_service.FirestoreError:
        logger.exception("Failed to read user record after creation for %s", uid)
        return None


def _user_from_iap_headers() -> Optional[dict[str, Any]]:
    email_header = request.headers.get("X-Goog-Authenticated-User-Email")
    if not email_header:
        return None
    _issuer, _, email = email_header.partition(":")
    email = email.strip().lower()
    if not email:
        return None

    raw_uid = request.headers.get("X-Goog-Authenticated-User-Id", "")
    _uid_issuer, _, uid = raw_uid.partition(":")
    uid = uid or email

    display_name = request.headers.get("X-Goog-Authenticated-User-Display-Name")
    name = display_name or email.split("@")[0]

    admin_emails = _admin_email_set()
    role = "admin" if email in admin_emails else "member"

    db_user = _sync_user_record(uid, email, name, role)
    if db_user:
        role = db_user.role or role
        name = db_user.name or name

    return {
        "uid": uid,
        "email": email,
        "name": name,
        "role": role,
        "is_mfa": False,
    }


def _user_from_firebase_tokens() -> Optional[dict[str, Any]]:
    auth_header = request.headers.get("Authorization")
    decoded: Optional[dict[str, Any]] = None

    if auth_header:
        try:
            id_token = auth_header.split(" ").pop()
            decoded = firebase_auth.verify_id_token(id_token, check_revoked=True)
        except Exception as exc:  # pragma: no cover - defensive logging
            current_app.logger.info(
                "verify_id_token failed: %s: %s", exc.__class__.__name__, exc
            )

    if decoded is None:
        cookie = request.cookies.get(FB_COOKIE)
        if not cookie:
            return None
        try:
            decoded = firebase_auth.verify_session_cookie(cookie, check_revoked=True)
        except Exception as exc:  # pragma: no cover - defensive logging
            current_app.logger.info(
                "verify_session_cookie failed: %s: %s", exc.__class__.__name__, exc
            )
            return None

    db_user = None
    try:
        db_client = getattr(users_service, "db", None)
        if db_client is not None and decoded.get("uid"):
            db_user_obj = users_service.get_user(decoded["uid"])
            if db_user_obj:
                db_user = db_user_obj.to_dict() | {"id": db_user_obj.id}
    except users_service.FirestoreError:
        logger.exception("Failed to load Firestore user for %s", decoded.get("uid"))

    return build_user_context(decoded, db_user=db_user)


def get_current_user() -> Optional[dict[str, Any]]:
    backend = _get_auth_backend()
    if backend == "firebase":
        return _user_from_firebase_tokens()
    if backend == "iap":
        return _user_from_iap_headers()
    return None


def get_current_user_from_token() -> Optional[dict[str, Any]]:
    """Backwards-compatible alias for existing imports."""
    return get_current_user()


def ensure_user() -> Optional[dict[str, Any]]:
    """Ensure g.user is populated with the current authenticated user context."""
    if not hasattr(g, "user") or g.user is None:
        g.user = get_current_user()
    return g.user


def require_roles(*roles: str):
    """Enforce authentication and optional role membership, returning a response if blocked."""
    if not current_app.config.get("AUTH_ENABLED", False):
        return None

    user = ensure_user()
    if not user:
        backend = _get_auth_backend()
        logger.info(
            "Unauthenticated access attempt blocked",
            extra={
                "auth_event": "auth_required_failure",
                "path": request.path,
                "ip": request.remote_addr,
                "backend": backend,
            },
        )
        if backend == "firebase":
            login_url = url_for("auth.login", next=request.full_path)
            return redirect(login_url, code=302)
        abort(401, description="Authentication required. Access is managed by IAP.")

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
    """Guard a route, returning the auth response when auth is active."""

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
