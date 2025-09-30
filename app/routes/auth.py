"""Authentication routes for Firebase session cookies."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from firebase_admin import auth as firebase_auth  # type: ignore[import-untyped]

from app.constants import FB_COOKIE
from app.extensions import csrf, limiter
from app.services import users as users_service

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

bp = auth_bp





@auth_bp.get("/login")
def login():
    next_path = request.args.get("next", "/")

    if not current_app.config.get("AUTH_ENABLED", False):
        return redirect(next_path or "/")

    firebase_config = {
        "apiKey": current_app.config.get("FIREBASE_WEB_API_KEY", ""),
        "authDomain": current_app.config.get("FIREBASE_AUTH_DOMAIN", ""),
        "projectId": current_app.config.get("FIREBASE_PROJECT_ID", ""),
    }

    missing = [key for key, value in firebase_config.items() if not value]
    if missing:
        logger.warning("Missing Firebase config keys: %s", ", ".join(missing))

    return render_template(
        "login.html",
        next_path=next_path,
        firebase_config=firebase_config,
    )


@auth_bp.post("/sessionLogin")
@csrf.exempt
@limiter.limit("20/minute")
def session_login():
    if not current_app.config.get("AUTH_ENABLED", False):
        return jsonify({"error": "auth disabled"}), 403

    session_lifetime_days = current_app.config.get("SESSION_COOKIE_LIFETIME_DAYS", 5)
    payload = request.get_json(silent=True) or request.form
    id_token = payload.get("idToken") if payload else None
    remember_me = payload.get("rememberMe") if payload else False
    next_url = payload.get("next") if payload else None
    if not next_url:
        next_url = request.args.get("next")

    if not id_token:
        return jsonify({"error": "missing idToken"}), 400

    if remember_me:
        session_lifetime_days = current_app.config.get(
            "SESSION_COOKIE_LIFETIME_REMEMBER_ME_DAYS", 14
        )

    expires_in = timedelta(days=session_lifetime_days)

    try:
        # Verify the ID token while checking if the token is revoked.
        decoded_id_token = firebase_auth.verify_id_token(id_token, check_revoked=True)
        # Create the session cookie.
        session_cookie = firebase_auth.create_session_cookie(
            id_token, expires_in=expires_in
        )
    except firebase_auth.ExpiredIdTokenError:
        logger.warning(
            "Expired ID token on session login",
            extra={
                "auth_event": "login_failure",
                "reason": "expired_token",
                "ip": request.remote_addr,
            },
        )
        return jsonify({"error": "expired idToken"}), 401
    except firebase_auth.InvalidIdTokenError:
        logger.warning(
            "Invalid ID token on session login",
            extra={
                "auth_event": "login_failure",
                "reason": "invalid_token",
                "ip": request.remote_addr,
            },
        )
        return jsonify({"error": "invalid idToken"}), 401
    except firebase_auth.RevokedIdTokenError:
        logger.warning(
            "Revoked ID token on session login",
            extra={
                "auth_event": "login_failure",
                "reason": "revoked_token",
                "ip": request.remote_addr,
            },
        )
        return jsonify({"error": "revoked idToken"}), 401
    except Exception as e:
        logger.error(f"Failed to create session cookie: {e}")
        return jsonify({"error": "internal server error"}), 500

    # Create user in Firestore if they don't exist
    try:
        transaction = users_service.db.transaction()
        user, is_new_user = users_service.get_or_create_user(
            transaction, decoded_id_token
        )
    except users_service.FirestoreError as e:
        logger.error(f"Failed to get or create user: {e}")
        return jsonify({"error": "internal server error"}), 500

    response = make_response(redirect(next_url or url_for("main.index")))
    response.set_cookie(
        FB_COOKIE,
        session_cookie,
        max_age=int(expires_in.total_seconds()),
        secure=True,
        httponly=True,
        samesite="None",
        path="/",
    )

    auth_method = decoded_id_token.get("firebase", {}).get(
        "sign_in_provider", "unknown"
    )
    logger.info(
        "User session created",
        extra={
            "auth_event": "login_success",
            "user_id": user.id,
            "email": user.email,
            "role": user.role,
            "auth_method": auth_method,
            "is_new_user": is_new_user,
            "ip": request.remote_addr,
        },
    )

    return response


@auth_bp.post("/logout")
def logout():
    # Basic protection to ensure the request is from an AJAX call
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        abort(403)

    user = g.user
    if not user:
        cookie = request.cookies.get(FB_COOKIE)
        if cookie:
            try:
                claims = firebase_auth.verify_session_cookie(cookie, check_revoked=True)
                g.claims = claims
                user = build_user_context(claims)
                g.user = user
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.info(
                    "Session cookie unavailable during logout",
                    extra={
                        "auth_event": "logout_cookie_missing",
                        "reason": str(exc),
                        "exception": exc.__class__.__name__,
                        "path": request.path,
                        "ip": request.remote_addr,
                    },
                )

    if user and user.get("uid"):
        try:
            firebase_auth.revoke_refresh_tokens(user.get("uid"))
            logger.info(
                "User refresh tokens revoked",
                extra={
                    "auth_event": "revoke_tokens",
                    "user_id": user.get("uid"),
                    "ip": request.remote_addr,
                },
            )
        except Exception as e:
            logger.warning(
                "Failed to revoke refresh tokens for user %s: %s", user.get("uid"), e
            )

    session.clear()
    response = make_response(jsonify({"status": "ok"}))
    response.set_cookie(
        FB_COOKIE,
        "",
        expires=0,
        secure=True,
        httponly=True,
        samesite="None",
        path="/",
    )

    if user:
        logger.info(
            "User session destroyed",
            extra={
                "auth_event": "logout_success",
                "user_id": user.get("uid"),
                "ip": request.remote_addr,
            },
        )

    flash("You have been signed out.", "info")
    return response
