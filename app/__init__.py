import os
import logging

import firebase_admin  # type: ignore[import-untyped]
from dotenv import load_dotenv
from flask import Flask, request, abort, redirect

from app.extensions import cache, limiter
from app.utils.logging_config import setup_logging
import app.utils.firestore_storage  # Ensures registration  # noqa: F401

# OpenTelemetry Imports for Tracing
# from opentelemetry import trace
# from opentelemetry.sdk.trace import TracerProvider
# from opentelemetry.sdk.trace.export import BatchSpanProcessor
# from opentelemetry.instrumentation.flask import FlaskInstrumentor
# from opentelemetry.exporter.gcp_trace import CloudTraceSpanExporter


def _validate_environment():
    """Check for required environment variables and raise RuntimeError if missing."""
    logger = logging.getLogger(__name__)
    required_vars = [
        "GCP_PROJECT_ID",
        "GCS_BUCKET",
    ]
    # FLASK_SECRET_KEY is also checked below, but we include it here for a clearer error message.
    if not os.getenv("FLASK_SECRET_KEY") and not os.getenv("SECRET_KEY"):
        required_vars.append("FLASK_SECRET_KEY")

    # In a production-like environment, some additional variables are required for tasks.
    if (
        os.getenv("ENV") != "development"
        and os.getenv("AUTH_ENABLED", "false").lower() == "true"
    ):
        required_vars.extend(
            [
                "SERVICE_ACCOUNT_EMAIL",
            ]
        )

    # If auth is enabled, Firebase variables are required.
    if os.getenv("AUTH_ENABLED", "false").lower() == "true":
        required_vars.extend(
            [
                "FIREBASE_PROJECT_ID",
                "FIREBASE_WEB_API_KEY",
                "FIREBASE_AUTH_DOMAIN",
            ]
        )

    missing_vars = sorted(list(set(var for var in required_vars if not os.getenv(var))))
    if missing_vars:
        message = f"Missing required environment variables: {', '.join(missing_vars)}"
        logger.critical(message)
        raise RuntimeError(message)


def create_app():
    """Create and configure an instance of the Flask application."""
    load_dotenv()

    # Set up logging as early as possible
    setup_logging()
    logger = logging.getLogger(__name__)

    _validate_environment()

    logger.info("Application starting with configuration:")
    logger.info(f"  ENV: {os.getenv('ENV')}")
    logger.info(f"  GCP_PROJECT_ID: {os.getenv('GCP_PROJECT_ID')}")
    logger.info(f"  GCS_BUCKET: {os.getenv('GCS_BUCKET')}")
    logger.info(f"  AUTH_ENABLED: {os.getenv('AUTH_ENABLED')}")
    logger.info(f"  CACHE_TYPE: {os.getenv('CACHE_TYPE')}")
    logger.info(f"  RATELIMIT_STORAGE_URI: {os.getenv('RATELIMIT_STORAGE_URI')}")

    # Set up OpenTelemetry Tracing
    # if os.getenv("ENV") == "production":
    #     trace.set_tracer_provider(TracerProvider())
    #     tracer_provider = trace.get_tracer_provider()
    #     tracer_provider.add_span_processor(
    #         BatchSpanProcessor(CloudTraceSpanExporter())
    #     )

    app = Flask(__name__, instance_relative_config=True)

    # Instrument the Flask app
    # if os.getenv("ENV") == "production":
    #     FlaskInstrumentor().instrument_app(app)

    # Load the secret key and cache configuration from the environment.
    try:
        default_timeout = int(os.getenv("CACHE_DEFAULT_TIMEOUT", "300"))
    except (TypeError, ValueError):
        default_timeout = 300

    cache_defaults = {
        "CACHE_TYPE": os.getenv("CACHE_TYPE", "SimpleCache"),
        "CACHE_DEFAULT_TIMEOUT": default_timeout,
        "CACHE_KEY_PREFIX": os.getenv("CACHE_KEY_PREFIX", "zissou"),
    }
    redis_url = os.getenv("CACHE_REDIS_URL")
    if redis_url:
        cache_defaults["CACHE_REDIS_URL"] = redis_url

    secret_key = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "dev"
    session_cookie_name = os.getenv("SESSION_COOKIE_NAME", "__zissou_session")
    is_prod = os.getenv("ENV") == "production"
    auth_enabled = os.getenv("AUTH_ENABLED", "false").lower() == "true"
    session_cookie_secure = (
        os.getenv("SESSION_COOKIE_SECURE", str(is_prod)).lower() == "true"
    )
    session_cookie_samesite = "None" if session_cookie_secure else "Lax"
    firebase_project_id = os.getenv("FIREBASE_PROJECT_ID")
    admin_emails_raw = os.getenv("ADMIN_EMAILS", "")
    admin_emails = [
        email.strip().lower() for email in admin_emails_raw.split(",") if email.strip()
    ]
    canonical_host = (
        os.getenv("CANONICAL_HOST") or os.getenv("CANON_DOMAIN") or ""
    ).strip()
    canonical_host = canonical_host.lower() or None

    # Determine storage URI for Flask-Limiter
    storage_uri = "memory://"
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("GOOGLE_CLOUD_PROJECT"):
        storage_uri = os.getenv("RATELIMIT_STORAGE_URI", "firestore://rate_limits")
    else:
        logger.warning("Rate limiter falling back to in-memory storage.")

    app.config.from_mapping(
        SECRET_KEY=secret_key,
        WTF_CSRF_ENABLED=True,
        WTF_CSRF_SECRET_KEY=os.getenv("CSRF_SECRET_KEY", "a-different-secret-key"),
        SESSION_COOKIE_NAME=session_cookie_name,
        SESSION_COOKIE_SECURE=session_cookie_secure,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE=session_cookie_samesite,
        AUTH_ENABLED=auth_enabled,
        FIREBASE_PROJECT_ID=firebase_project_id,
        FIREBASE_WEB_API_KEY=os.getenv("FIREBASE_WEB_API_KEY"),
        FIREBASE_AUTH_DOMAIN=os.getenv("FIREBASE_AUTH_DOMAIN"),
        ADMIN_EMAILS=admin_emails,
        RATELIMIT_STORAGE_URI=storage_uri,
        CANONICAL_HOST=canonical_host,
        **cache_defaults,
    )

    if auth_enabled and not firebase_project_id:
        raise RuntimeError("FIREBASE_PROJECT_ID must be set when AUTH_ENABLED is true")

    if firebase_project_id and not firebase_admin._apps:
        firebase_admin.initialize_app()

    from app.extensions import csrf

    csrf.init_app(app)

    limiter.init_app(app)

    from flask_talisman import Talisman

    content_security_policy = {
        "default-src": ["'self'"],
        "script-src": [
            "'self'",
            "https://www.gstatic.com",
            "https://www.googleapis.com",
        ],
        "connect-src": [
            "'self'",
            "https://www.googleapis.com",
            "https://identitytoolkit.googleapis.com",
            "https://securetoken.googleapis.com",
        ],
        "style-src": ["'self'", "'unsafe-inline'"],
        "img-src": ["'self'", "data:"],
        "font-src": ["'self'", "https://fonts.gstatic.com"],
    }

    Talisman(app, content_security_policy=content_security_policy)

    cache.init_app(app)

    # Ensure the instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    # Register Blueprints
    from .routes import admin, auth, feeds, main

    if "main" not in app.blueprints:
        app.register_blueprint(main.bp)
        print("Registered main blueprint")
    if "feeds" not in app.blueprints:
        app.register_blueprint(feeds.bp)
        print("Registered feeds blueprint")
    if "admin" not in app.blueprints:
        app.register_blueprint(admin.bp)
        print("Registered admin blueprint")
    if "auth" not in app.blueprints:
        app.register_blueprint(auth.auth_bp)
        print("Registered auth blueprint")

    if canonical_host:

        @app.before_request
        def enforce_canonical_host():
            """Redirect to the configured canonical host to avoid cookie scope issues."""
            if app.config.get("ENV") == "development":
                return None

            host = (request.host or "").split(":")[0].lower()
            if not host or host == canonical_host:
                return None

            if host in {"localhost", "127.0.0.1"}:
                return None

            target_path = request.full_path if request.query_string else request.path
            if target_path.endswith("?"):
                target_path = target_path[:-1]

            redirect_url = f"https://{canonical_host}{target_path}"
            return redirect(redirect_url, code=301)

    @app.before_request
    def block_wordpress_bots():
        """Block common WordPress bot paths."""
        if request.path.startswith(("/wp-admin", "/xmlrpc.php", "/wp-login.php")):
            abort(403)

    # Conditionally register the task handler blueprint
    # This is to avoid running the task handler in a local dev environment
    # where it is not needed and may not have the right credentials.
    if os.getenv("ENV") != "development":
        from .routes import tasks

        app.register_blueprint(tasks.bp)

    # Register Jinja2 filters
    from app.utils.jinja_filters import (
        format_duration,
        format_datetime,
        url_host,
        nl2p,
        tag_color_class,
        merge_dicts,
    )

    app.jinja_env.filters["format_duration"] = format_duration
    app.jinja_env.filters["format_datetime"] = format_datetime
    app.jinja_env.filters["nl2p"] = nl2p
    app.jinja_env.filters["tag_color_class"] = tag_color_class
    app.jinja_env.filters["merge"] = merge_dicts

    # Register global functions for templates
    app.jinja_env.globals["url_host"] = url_host

    # Register error handlers
    from flask import render_template

    def internal_server_error(e):
        # Note: we set the status code explicitly
        return render_template("500.html", error_message=str(e)), 500

    def forbidden(e):
        return render_template("403.html"), 403

    app.register_error_handler(500, internal_server_error)
    app.register_error_handler(403, forbidden)

    # A simple hello page
    @app.route("/hello")
    def hello():
        return "Hello, World!"

    return app
