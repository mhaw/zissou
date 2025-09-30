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
)
from firebase_admin import auth as firebase_auth  # type: ignore[import-untyped]

from app.auth import build_user_context, _verify_session_cookie
from app.extensions import csrf, limiter
from app.services import users as users_service

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

bp = auth_bp


@auth_bp.before_app_request
def refresh_session_cookie():
    """Proactively refresh the session cookie if it is close to expiring."""
    user_ctx = getattr(g, "user", None)
    if user_ctx and request.endpoint not in ["static", "auth.logout"]:
        claims = g.get("claims")
        if claims:
            expiry_time = datetime.fromtimestamp(claims["exp"])
            now = datetime.utcnow()
            threshold = timedelta(hours=24)

            if expiry_time - now < threshold:
                try:
                    new_session_cookie = firebase_auth.create_session_cookie(
                        claims["sub"], expires_in=timedelta(days=5)
                    )
                    response = make_response(redirect(request.url))
                    response.set_cookie(
                        current_app.config["SESSION_COOKIE_NAME"],
                        new_session_cookie,
                        httponly=True,
                        secure=current_app.config["SESSION_COOKIE_SECURE"],
                        samesite="None",
                        path="/",
                    )
                    return response
                except Exception as e:
                    logger.warning(f"Failed to refresh session cookie: {e}")


@auth_bp.before_app_request
def attach_authenticated_user():
    """Attach the authenticated user to the request context."""
    g.claims = None
    g.user = None

    if not current_app.config.get("AUTH_ENABLED", False):
        return None

    claims = _verify_session_cookie()
    if claims:
        g.claims = claims
        g.user = build_user_context(claims)
    return None


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

    session_cookie_name = current_app.config.get(
        "SESSION_COOKIE_NAME", "__zissou_session"
    )
    session_lifetime_days = current_app.config.get("SESSION_COOKIE_LIFETIME_DAYS", 5)
    payload = request.get_json(silent=True) or request.form
    id_token = payload.get("idToken") if payload else None
    remember_me = payload.get("rememberMe") if payload else False

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

    user_context = build_user_context(decoded_id_token, user.to_dict())

    response_data = {"status": "ok", "user": user.to_dict(), "is_new_user": is_new_user}
    response = make_response(jsonify(response_data))
    secure_flag = current_app.config.get("SESSION_COOKIE_SECURE", True)

    response.set_cookie(
        session_cookie_name,
        session_cookie,
        max_age=int(expires_in.total_seconds()),
        httponly=True,
        secure=secure_flag,
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
            "ip": request.remote_addr,
        },
    )

    if is_new_user:
        flash(
            f"Welcome to Zissou, {user_context.get('name') or user_context.get('email')}! Please take a moment to set your preferences.",
            "success",
        )
    else:
        flash(
            f"Welcome back, {user_context.get('name') or user_context.get('email')}!",
            "success",
        )
    return response


@auth_bp.post("/logout")
def logout():
    # Basic protection to ensure the request is from an AJAX call
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        abort(403)

    user = g.user
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
    session_cookie_name = current_app.config.get(
        "SESSION_COOKIE_NAME", "__zissou_session"
    )

    response = make_response(jsonify({"status": "ok"}))
    secure_flag = current_app.config.get("SESSION_COOKIE_SECURE", True)
    response.set_cookie(
        session_cookie_name,
        "",
        expires=0,
        max_age=0,
        path="/",
        secure=secure_flag,
        httponly=True,
        samesite="None",
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
