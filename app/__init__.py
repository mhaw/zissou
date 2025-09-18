from flask import Flask
from dotenv import load_dotenv
import os
from app.utils.logging_config import setup_logging
from app.extensions import cache

# OpenTelemetry Imports for Tracing
# from opentelemetry import trace
# from opentelemetry.sdk.trace import TracerProvider
# from opentelemetry.sdk.trace.export import BatchSpanProcessor
# from opentelemetry.instrumentation.flask import FlaskInstrumentor
# from opentelemetry.exporter.gcp_trace import CloudTraceSpanExporter


def create_app():
    """Create and configure an instance of the Flask application."""
    load_dotenv()

    # Set up logging as early as possible
    setup_logging()

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

    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY"),
        **cache_defaults,
    )

    cache.init_app(app)

    # Ensure the instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    # Register Blueprints
    from .routes import main, feeds, admin

    app.register_blueprint(main.bp)
    app.register_blueprint(feeds.bp)
    app.register_blueprint(admin.bp)

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

    app.register_error_handler(500, internal_server_error)

    # A simple hello page
    @app.route("/hello")
    def hello():
        return "Hello, World!"

    return app
