import os

from flask import Blueprint, current_app, jsonify, send_from_directory

from app.services import health as health_service

bp = Blueprint("utility", __name__)


@bp.route("/favicon.ico")
def favicon():
    static_dir = os.path.join(current_app.root_path, "static", "img")
    try:
        return send_from_directory(
            static_dir,
            "zissou_favicon.png",
            mimetype="image/png",
            max_age=60 * 60 * 24 * 30,
        )
    except FileNotFoundError:
        return ("", 204)


@bp.route("/health")
def health():
    """Return structured health status for downstream services."""
    results, overall_healthy = health_service.check_all_services()
    status_code = 200 if overall_healthy else 503
    return jsonify(results), status_code


@bp.route("/healthz")
def healthz():
    """Lightweight liveness probe."""
    return "ok", 200


@bp.route("/robots.txt")
def robots_txt():
    static_dir = os.path.join(current_app.root_path, "static")
    try:
        return send_from_directory(
            static_dir,
            "robots.txt",
            mimetype="text/plain",
            max_age=60 * 60,
        )
    except FileNotFoundError:
        response = current_app.response_class(
            "User-agent: *\nDisallow:\n", mimetype="text/plain"
        )
        return response


@bp.route("/wp-admin/<path:_>")
@bp.route("/wordpress/<path:_>")
@bp.route("/xmlrpc.php")
def _wp_block(_=None):
    """Return a 410 Gone for obvious WordPress probes."""
    return ("", 410)
