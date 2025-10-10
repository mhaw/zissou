import logging
import os
import traceback

import firebase_admin  # type: ignore[import-untyped]
import flask_limiter
from dotenv import load_dotenv
from flask import Flask, g, request, redirect, send_from_directory

from app.extensions import cache, limiter
from app.utils.firestore_cache import FirestoreCache
from app.utils.logging_config import setup_logging
import app.utils.firestore_storage  # Ensures registration  # noqa: F401
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import DefaultCredentialsError
from limits.storage import storage_from_string

# OpenTelemetry Imports for Tracing
# from opentelemetry import trace
# from opentelemetry.sdk.trace import TracerProvider
# from opentelemetry.sdk.trace.export import BatchSpanProcessor
# from opentelemetry.instrumentation.flask import FlaskInstrumentor
# from opentelemetry.exporter.gcp_trace import CloudTraceSpanExporter


from app.config import CSRFConfig, FirebaseAuthConfig


def _validate_environment(firebase_auth_config: FirebaseAuthConfig):
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

    # If auth is enabled, make sure all client configuration exists.
    if (
        os.getenv("AUTH_ENABLED", "false").lower() == "true"
        and not firebase_auth_config.is_valid
    ):
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


def _parse_csp_values(raw_value: str | None) -> list[str]:
    """Split comma/space separated CSP env overrides into a clean list."""
    if not raw_value:
        return []
    return [
        fragment.strip()
        for fragment in raw_value.replace(",", " ").split()
        if fragment.strip()
    ]


def init_extensions(app, default_timeout: int) -> None:
    """Configure cache, limiter, and related extensions."""
    env_name = (app.config.get("ENV") or "").strip().lower()
    limiter_version = getattr(flask_limiter, "__version__", "0")
    app.logger.info("Flask-Limiter version: %s", limiter_version)
    cache_type_env = (os.getenv("CACHE_TYPE") or "").strip()

    # Determine cache backend: explicit env takes precedence, otherwise default by env.
    if cache_type_env:
        selected_cache_type = cache_type_env
    elif env_name == "production":
        selected_cache_type = "RedisCache"
    else:
        selected_cache_type = "SimpleCache"

    cache_config: dict[str, str | int] = {
        "CACHE_TYPE": selected_cache_type,
        "CACHE_DEFAULT_TIMEOUT": default_timeout,
    }

    if selected_cache_type.lower() in {"redis", "rediscache"}:
        redis_url = (os.getenv("CACHE_REDIS_URL") or os.getenv("REDIS_URL") or "").strip()
        if redis_url:
            cache_config["CACHE_TYPE"] = "RedisCache"
            cache_config["CACHE_REDIS_URL"] = redis_url
        else:
            if env_name == "production":
                app.logger.error(
                    "CACHE_TYPE is RedisCache but CACHE_REDIS_URL is not set; falling back to SimpleCache."
                )
            cache_config["CACHE_TYPE"] = "SimpleCache"
    elif selected_cache_type.lower() in {"filesystem", "filesystemcache"}:
        cache_dir = os.getenv("CACHE_DIR") or os.path.join(app.instance_path, "cache")
        os.makedirs(cache_dir, exist_ok=True)
        cache_config["CACHE_TYPE"] = "FileSystemCache"
        cache_config["CACHE_DIR"] = cache_dir
    elif selected_cache_type.lower() in {"firestore", "firestorecache"}:
        if hasattr(app, "firestore_client") and app.firestore_client:
            collection = os.getenv("CACHE_FIRESTORE_COLLECTION", "zissou-cache")
            cache_config["CACHE_TYPE"] = FirestoreCache(
                client=app.firestore_client,
                collection=collection,
                default_timeout=default_timeout,
            )
        else:
            app.logger.error(
                "CACHE_TYPE is Firestore but Firestore client is not available; falling back to SimpleCache."
            )
            cache_config["CACHE_TYPE"] = "SimpleCache"
    elif selected_cache_type.lower() == "nullcache":
        cache_config["CACHE_TYPE"] = "NullCache"
    elif selected_cache_type.lower() not in {"simplecache", "nullcache"}:
        # Unrecognised backend; fall back to SimpleCache to keep the service running.
        app.logger.error(
            "Unsupported CACHE_TYPE '%s'; falling back to SimpleCache.", selected_cache_type
        )
        cache_config["CACHE_TYPE"] = "SimpleCache"

    cache.init_app(app, config=cache_config)
    app.config.update(cache_config)
    app.logger.info("Configured Flask-Caching backend: %s", app.config["CACHE_TYPE"])
    app.logger.info("Cache backend: %s", app.config["CACHE_TYPE"])

    storage_uri_env = (os.getenv("RATELIMIT_STORAGE_URI") or "").strip()
    storage_uri = storage_uri_env
    if not storage_uri:
        if app.config["CACHE_TYPE"] == "RedisCache" and app.config.get("CACHE_REDIS_URL"):
            storage_uri = app.config["CACHE_REDIS_URL"]
        elif env_name == "production":
            storage_uri = "memory://"
        else:
            storage_uri = "memory://"

    try:
        storage = storage_from_string(storage_uri)
    except Exception as exc:  # pragma: no cover - fail-safe for boot issues
        app.logger.error(
            "Failed to initialize rate limiter storage '%s': %s. Falling back to memory://",
            storage_uri,
            exc,
        )
        storage_uri = "memory://"
        storage = storage_from_string(storage_uri)

    app.config["RATELIMIT_STORAGE_URI"] = storage_uri

    limiter.init_app(app)

    app.logger.info("Rate limit storage initialized: %s", storage_uri)
    app.logger.info("Rate limiter storage: %s", app.config["RATELIMIT_STORAGE_URI"])


def create_app():
    """Create and configure an instance of the Flask application."""
    load_dotenv()

    # Set up logging as early as possible
    setup_logging()
    logger = logging.getLogger(__name__)

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    alias_project_id = os.getenv("GCP_PROJECT_ID") or os.getenv("GCLOUD_PROJECT")
    if not project_id and alias_project_id:
        os.environ["GOOGLE_CLOUD_PROJECT"] = alias_project_id
        project_id = alias_project_id
    if project_id:
        os.environ.setdefault("GCLOUD_PROJECT", project_id)

    firebase_auth_config = FirebaseAuthConfig.from_env()
    csrf_config = CSRFConfig.from_env()
    _validate_environment(firebase_auth_config)

    logger.info("Application starting with configuration:")
    logger.info(f"  ENV: {os.getenv('ENV')}")
    logger.info(f"  GCP_PROJECT_ID: {os.getenv('GCP_PROJECT_ID')}")
    logger.info(f"  GOOGLE_CLOUD_PROJECT: {os.getenv('GOOGLE_CLOUD_PROJECT')}")
    logger.info(f"  GCS_BUCKET: {os.getenv('GCS_BUCKET')}")
    logger.info(f"  AUTH_ENABLED: {os.getenv('AUTH_ENABLED')}")
    logger.info(f"  CACHE_TYPE (env): {os.getenv('CACHE_TYPE')}")
    logger.info(f"  RATELIMIT_STORAGE_URI (env): {os.getenv('RATELIMIT_STORAGE_URI')}")

    # Set up OpenTelemetry Tracing
    # if os.getenv("ENV") == "production":
    #     trace.set_tracer_provider(TracerProvider())
    #     tracer_provider = trace.get_tracer_provider()
    #     tracer_provider.add_span_processor(
    #         BatchSpanProcessor(CloudTraceSpanExporter())
    #     )

    app = Flask(__name__, instance_relative_config=True)
    # Wrap with whitenoise for static file serving

    # Instrument the Flask app
    # if os.getenv("ENV") == "production":
    #     FlaskInstrumentor().instrument_app(app)

    # Load the secret key and cache configuration from the environment.
    try:
        default_timeout = int(os.getenv("CACHE_DEFAULT_TIMEOUT", "300"))
    except (TypeError, ValueError):
        default_timeout = 300

    secret_key = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "dev"
    env_name = os.getenv("ENV", "").strip().lower()
    is_development = env_name == "development"
    if is_development:
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    auth_enabled_raw = os.getenv("AUTH_ENABLED")
    auth_enabled = False
    if isinstance(auth_enabled_raw, str):
        auth_enabled = auth_enabled_raw.strip().lower() == "true"
    flask_session_secure_env = os.getenv("FLASK_SESSION_COOKIE_SECURE")
    if flask_session_secure_env is not None:
        flask_session_secure = flask_session_secure_env.strip().lower() == "true"
    else:
        flask_session_secure = True
    if not flask_session_secure and not is_development:
        logger.warning(
            "FLASK_SESSION_COOKIE_SECURE is disabled outside development; browsers may drop session cookies."
        )

    admin_emails_raw = os.getenv("ADMIN_EMAILS", "")
    admin_emails = [
        email.strip().lower() for email in admin_emails_raw.split(",") if email.strip()
    ]
    canonical_host = (
        os.getenv("CANONICAL_HOST") or os.getenv("CANON_DOMAIN") or ""
    ).strip()
    canonical_host = canonical_host.lower() or None

    app.config.from_mapping(
        SECRET_KEY=secret_key,
        WTF_CSRF_ENABLED=True,
        WTF_CSRF_SECRET_KEY=csrf_config.secret_key,
        WTF_CSRF_TIME_LIMIT=csrf_config.time_limit_seconds,
        SESSION_COOKIE_NAME="flask_session",
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        AUTH_ENABLED=auth_enabled,
        FIREBASE_AUTH_CONFIG=firebase_auth_config,
        RATELIMIT_STORAGE_URI=os.getenv("RATELIMIT_STORAGE_URI"),
        CANONICAL_HOST=canonical_host,
    )

    from app.utils.firestore_session import FirestoreSessionInterface
    from google.cloud import firestore

    firestore_kwargs: dict[str, str] = {}
    if project_id:
        firestore_kwargs["project"] = project_id

    if os.getenv("FIRESTORE_EMULATOR_HOST"):
        logger.info(
            "Firestore emulator detected at %s", os.getenv("FIRESTORE_EMULATOR_HOST")
        )

    firestore_client = None
    try:
        firestore_client = firestore.Client(**firestore_kwargs)
    except (DefaultCredentialsError, GoogleAPICallError) as exc:
        logger.error("Failed to initialize Firestore session store: %s", exc)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception(
            "Unexpected error while initializing Firestore session store: %s", exc
        )

    # Attach the client to the app for use in extensions.
    app.firestore_client = firestore_client

    # Initialize extensions like caching and rate limiting *after* the client is ready.
    init_extensions(app, default_timeout)

    # Now, set up the session interface.
    app.session_interface = FirestoreSessionInterface(firestore_client, "sessions")

    if (
        app.config.get("AUTH_ENABLED")
        and not app.config.get("FIREBASE_AUTH_CONFIG").is_valid
    ):
        raise RuntimeError(
            "Firebase authentication is enabled, but the configuration is invalid."
        )

    if app.config.get("AUTH_ENABLED") and not firebase_admin._apps:
        firebase_admin.initialize_app()

    from app.extensions import csrf
    from flask_cors import CORS  # Import CORS

    csrf.init_app(app)

    # New: CORS configuration
    allowed_origins_raw = os.getenv("ALLOWED_ORIGINS", "").strip()
    allowed_origins = [
        origin.strip() for origin in allowed_origins_raw.split(",") if origin.strip()
    ]
    # Add default origins for local development and Cloud Run
    if "http://localhost:5000" not in allowed_origins:
        allowed_origins.append("http://localhost:5000")
    if "http://127.0.0.1:5000" not in allowed_origins:
        allowed_origins.append("http://127.0.0.1:5000")
    if (
        os.getenv("ENV") != "production"
    ):  # Allow all for non-prod, or more specific for Cloud Run dev
        allowed_origins.append("https://*.run.app")  # For Cloud Run development

    # Add canonical host if present
    canonical_host = app.config.get("CANONICAL_HOST")
    if canonical_host and f"https://{canonical_host}" not in allowed_origins:
        allowed_origins.append(f"https://{canonical_host}")

    app.config["ALLOWED_ORIGINS"] = allowed_origins

    CORS(app, supports_credentials=True, origins=app.config["ALLOWED_ORIGINS"])

    from flask_talisman import Talisman

    Talisman(
        app,
        content_security_policy=None,
        content_security_policy_nonce_in=["script-src", "style-src"],
    )

    report_to_header = os.getenv("REPORT_TO_HEADER")
    if report_to_header:

        @app.after_request
        def add_report_to_header(response):
            response.headers.setdefault("Report-To", report_to_header)
            return response

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

    from app.auth import get_current_user

    @app.before_request
    def attach_authenticated_user():
        """Attach the authenticated user to the request context."""
        g.user = None
        if request.path.startswith(
            ("/static/", "/auth/", "/favicon.ico", "/robots.txt", "/tasks/")
        ):
            return

        if not app.config.get("AUTH_ENABLED", False):
            return

        g.user = get_current_user()

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

    @app.route("/favicon.ico")
    def favicon():
        static_dir = os.path.join(app.root_path, "static", "img")
        try:
            return send_from_directory(
                static_dir,
                "zissou_favicon.png",
                mimetype="image/png",
                max_age=60 * 60 * 24 * 30,
            )
        except FileNotFoundError:
            return ("", 204)

    @app.route("/robots.txt")
    def robots_txt():
        static_dir = os.path.join(app.root_path, "static")
        try:
            return send_from_directory(
                static_dir,
                "robots.txt",
                mimetype="text/plain",
                max_age=60 * 60,
            )
        except FileNotFoundError:
            response = app.response_class(
                "User-agent: *\nDisallow:\n", mimetype="text/plain"
            )
            return response

    @app.route("/wp-admin/<path:_>")
    @app.route("/wordpress/<path:_>")
    @app.route("/xmlrpc.php")
    def _wp_block(_=None):
        """Return a 410 Gone for obvious WordPress probes."""
        return ("", 410)

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
        logger = logging.getLogger(__name__)
        # Log the full traceback as a string for better visibility in logs
        logger.error("An internal server error occurred: %s", e, exc_info=True)
        full_traceback = traceback.format_exc()
        logger.error("Full traceback:\n%s", full_traceback)
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
