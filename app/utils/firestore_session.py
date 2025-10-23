from __future__ import annotations

import uuid
from datetime import datetime, timezone

from flask.sessions import (
    SessionInterface,
    SessionMixin,
    SecureCookieSessionInterface,
)
from google.cloud import firestore
from google.api_core.exceptions import Forbidden
from werkzeug.datastructures import CallbackDict


class FirestoreSession(CallbackDict, SessionMixin):

    def __init__(self, initial=None, sid=None, new=False):
        def on_update(self):
            self.modified = True

        CallbackDict.__init__(self, initial, on_update)
        self.sid = sid
        self.new = new
        self.modified = False


class FirestoreSessionInterface(SessionInterface):
    session_class = FirestoreSession

    def __init__(self, db: firestore.Client | None, collection: str):
        self.db = db
        self.collection = collection
        self._fallback_interface = SecureCookieSessionInterface()
        self._warned_missing_db = False

    def _should_use_firestore(self, app) -> bool:
        """Return True when Firestore-backed sessions should be used."""
        if app.config.get("TESTING", False):
            return False
        return self.db is not None

    def open_session(self, app, request):
        if not self._should_use_firestore(app):
            if (
                self.db is None
                and not self._warned_missing_db
                and not app.config.get("TESTING", False)
            ):
                app.logger.warning(
                    "Firestore session backend unavailable; using secure cookie sessions."
                )
                self._warned_missing_db = True
            return self._fallback_interface.open_session(app, request)

        cookie_name = app.config.get("SESSION_COOKIE_NAME", "flask_session")
        sid = request.cookies.get(cookie_name)
        if not sid:
            sid = str(uuid.uuid4())
            return self.session_class(sid=sid, new=True)

        try:
            doc_ref = self.db.collection(self.collection).document(sid)
            doc = doc_ref.get()
        except Exception as exc:  # pragma: no cover - defensive logging
            app.logger.warning(
                "Failed to load session %s from Firestore; falling back to secure cookie. Error: %s",
                sid,
                exc,
            )
            return self._fallback_interface.open_session(app, request)

        if doc.exists:
            data = doc.to_dict() or {}
            expiration = data.get("expiration")
            now = datetime.now(timezone.utc)
            if isinstance(expiration, datetime):
                if expiration.tzinfo is None:
                    expiration = expiration.replace(tzinfo=timezone.utc)
                else:
                    expiration = expiration.astimezone(timezone.utc)
                if expiration > now:
                    data["expiration"] = expiration
                    return self.session_class(data, sid=sid)

        return self.session_class(sid=sid, new=True)

    def save_session(self, app, session, response):
        if not isinstance(
            session, self.session_class
        ) or not self._should_use_firestore(app):
            self._fallback_interface.save_session(app, session, response)
            return

        domain = self.get_cookie_domain(app)
        cookie_name = app.config.get("SESSION_COOKIE_NAME", "flask_session")
        if not session:
            response.delete_cookie(cookie_name, domain=domain)
            return

        if session.modified:
            session_data = dict(session)
            session_data["expiration"] = (
                datetime.now(timezone.utc) + app.permanent_session_lifetime
            )
            doc_ref = self.db.collection(self.collection).document(session.sid)
            # Attempt to save session with retries
            max_retries = 3
            base_delay = 0.5  # seconds
            for i in range(max_retries):
                try:
                    doc_ref.set(session_data, timeout=30)
                    break  # Success, exit retry loop
                except Forbidden as exc:
                    app.logger.error(
                        "Firestore denied session write for %s: %s. Verify the Cloud Run service account has the roles/datastore.user permission.",
                        session.sid,
                        exc,
                    )
                    self._fallback_interface.save_session(app, session, response)
                    return
                except Exception as exc:
                    if i < max_retries - 1:
                        delay = base_delay * (2**i)
                        app.logger.warning(
                            "Transient error persisting session %s to Firestore (attempt %d/%d): %s. Retrying in %.2f seconds.",
                            session.sid,
                            i + 1,
                            max_retries,
                            exc,
                            delay,
                        )
                        import time
                        time.sleep(delay)
                    else:
                        app.logger.warning(
                            "Failed to persist session %s to Firestore after %d attempts; falling back to secure cookie. Error: %s",
                            session.sid,
                            max_retries,
                            exc,
                        )
                        self._fallback_interface.save_session(app, session, response)
                        return

        response.set_cookie(
            cookie_name,
            session.sid,
            expires=self.get_expiration_time(app, session),
            httponly=True,
            domain=domain,
            path=self.get_cookie_path(app),
            secure=self.get_cookie_secure(app),
            samesite=self.get_cookie_samesite(app),
        )
