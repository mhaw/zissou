"""Authentication routes for Firebase session cookies."""
from __future__ import annotations

import logging
from datetime import timedelta

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
)
from firebase_admin import auth as firebase_auth  # type: ignore[import-untyped]

from app.auth import build_user_context
from app.models.user import User
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
def session_login():
    if not current_app.config.get("AUTH_ENABLED", False):
        return jsonify({"error": "auth disabled"}), 403

    session_cookie_name = current_app.config.get(
        "SESSION_COOKIE_NAME", "__zissou_session"
    )
    session_lifetime_days = current_app.config.get(
        "SESSION_COOKIE_LIFETIME_DAYS", 5
    )
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
        session_cookie = firebase_auth.create_session_cookie(id_token, expires_in=expires_in)
    except firebase_auth.InvalidIdTokenError:
        return jsonify({"error": "invalid idToken"}), 401
    except firebase_auth.RevokedIdTokenError:
        return jsonify({"error": "revoked idToken"}), 401
    except Exception as e:
        logger.error(f"Failed to create session cookie: {e}")
        return jsonify({"error": "internal server error"}), 500

    user_context = build_user_context(decoded_id_token)
    session["user"] = user_context

    # Create user in Firestore if they don't exist
    user = users_service.get_user(decoded_id_token["uid"])
    if not user:
        new_user = User(
            id=decoded_id_token["uid"],
            email=decoded_id_token.get("email"),
            name=decoded_id_token.get("name"),
            role=user_context.get("role"),
        )
        users_service.create_user(new_user)

    response = make_response(jsonify({"status": "ok"}))
    secure_flag = current_app.config.get("SESSION_COOKIE_SECURE", True)
    response.set_cookie(
        session_cookie_name,
        session_cookie,
        max_age=int(expires_in.total_seconds()),
        httponly=True,
        secure=secure_flag,
        samesite="Lax",
        path="/",
    )

    logger.info(
        "Firebase session created",
        extra={
            "uid": decoded_id_token.get("uid"),
            "email": decoded_id_token.get("email"),
            "role": user_context.get("role"),
        },
    )

    flash(f"Welcome, {user_context.get('name') or user_context.get('email')}!", "success")
    return response


@auth_bp.post("/logout")
def logout():
    user = session.get("user")
    if user and user.get("uid"):
        try:
            firebase_auth.revoke_refresh_tokens(user.get("uid"))
            logger.info("Revoked refresh tokens for user %s", user.get("uid"))
        except Exception as e:
            logger.warning(
                "Failed to revoke refresh tokens for user %s: %s", user.get("uid"), e
            )

    session.pop("user", None)
    session_cookie_name = current_app.config.get(
        "SESSION_COOKIE_NAME", "__zissou_session"
    )

    response = make_response(jsonify({"status": "ok"}))
    secure_flag = current_app.config.get("SESSION_COOKIE_SECURE", True)
    response.delete_cookie(session_cookie_name, path="/", secure=secure_flag)

    flash("You have been signed out.", "info")
    return response

    return response
