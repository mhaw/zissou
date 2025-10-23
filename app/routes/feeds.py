import hashlib
import logging
from typing import Any, Dict

from flask import (
    Blueprint,
    Response,
    g,
    make_response,
    render_template,
    request,
    url_for,
)

from app.extensions import cache
from app.services.buckets import list_buckets
from app.services.feeds import (
    FeedGenerationError,
    FeedIndexBuildingError,
    build_public_feed_schema,
    build_public_feed_xml,
    generate_feed_for_bucket,
    get_public_feed_items,
    get_public_feed_metadata,
    get_public_feed_subscription_links,
    normalise_public_feed_filters,
)

logger = logging.getLogger(__name__)

RSS_MIMETYPE = "application/rss+xml; charset=utf-8"

bp = Blueprint("feeds", __name__, url_prefix="/feeds")
public_bp = Blueprint("public_feed", __name__)


def _is_admin_user() -> bool:
    user = getattr(g, "user", None)
    if not user:
        return False
    role = getattr(user, "role", None)
    if role is None and isinstance(user, dict):
        role = user.get("role")
    return role == "admin"


def _render_index_building_response(
    error: FeedIndexBuildingError,
    *,
    feed_label: str,
    bucket_slug: str | None = None,
) -> Response:
    context = {
        "bucket_slug": bucket_slug,
        "feed_label": feed_label,
        "feed_url": request.url,
        "hint": error.hint if _is_admin_user() else None,
    }
    response = make_response(
        render_template("errors/index_building.html", **context),
        503,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Retry-After"] = "120"
    return response


@bp.route("/")
def feed_list():
    """List publicly discoverable bucket feeds."""
    try:
        buckets = list_buckets()
    except Exception:
        logger.exception("feeds.directory.buckets_failed")
        buckets = []

    public_buckets = [
        bucket
        for bucket in buckets
        if getattr(bucket, "is_public", False) or getattr(bucket, "public", False)
    ]
    public_buckets.sort(key=lambda bucket: (bucket.name or bucket.slug or "").lower())
    response = make_response(
        render_template(
            "feeds/index.html",
            buckets=public_buckets,
        )
    )
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


def _render_bucket_feed(bucket_slug: str, *, require_audio: bool) -> Response:
    try:
        page = request.args.get("page", 1, type=int)
        feed_base_url = request.base_url
        feed_xml = generate_feed_for_bucket(
            bucket_slug,
            feed_base_url,
            page,
            require_audio=require_audio,
        )

        etag = hashlib.sha1(feed_xml).hexdigest()
        if request.headers.get("If-None-Match") == etag:
            return Response(status=304)

        resp = Response(feed_xml, mimetype=RSS_MIMETYPE)
        resp.headers["Cache-Control"] = "public, max-age=900"
        resp.headers["ETag"] = etag
        return resp

    except FeedIndexBuildingError:
        raise
    except FeedGenerationError as exc:
        logger.info("Feed generation error for bucket %s: %s", bucket_slug, exc)
        return Response(
            f"<error>Could not generate feed for {bucket_slug}. Error: {exc}</error>",
            status=404,
            mimetype="application/xml",
        )
    except Exception:
        logger.exception("Error generating feed for bucket: %s", bucket_slug)
        return Response(
            f"<error>Could not generate feed for {bucket_slug}. An unexpected error occurred.</error>",
            status=500,
            mimetype="application/xml",
        )


@bp.route("/<bucket_slug>.xml")
@cache.cached(
    key_prefix=lambda: f"feed_{request.view_args['bucket_slug']}_{request.args.get('page', 1, type=int)}",
    timeout=900,
)
def bucket_feed(bucket_slug: str):
    """Podcast-oriented RSS feed (audio required)."""
    return _render_bucket_feed(bucket_slug, require_audio=True)


@bp.route("/<bucket_slug>.links.xml")
@cache.cached(
    key_prefix=lambda: f"feed_links_{request.view_args['bucket_slug']}_{request.args.get('page', 1, type=int)}",
    timeout=900,
)
def bucket_links_feed(bucket_slug: str):
    """Link-style RSS feed including items without audio."""
    return _render_bucket_feed(bucket_slug, require_audio=False)


@public_bp.route("/feed.xml")
@cache.cached(timeout=300, query_string=True)
def public_feed() -> Response:
    """Expose a public RSS feed aggregated across all published items."""
    filters = normalise_public_feed_filters(
        tag=request.args.get("tag"),
        days=request.args.get("days"),
    )
    items = get_public_feed_items(filters)
    rss_query = {k: str(v) for k, v in filters.items()}
    feed_url = url_for("public_feed.public_feed", _external=True, **rss_query)

    try:
        feed_xml = build_public_feed_xml(
            items=items, feed_url=feed_url, filters=filters
        )
    except FeedIndexBuildingError:
        raise
    except Exception:
        logger.exception("Failed to build public feed XML", extra={"filters": filters})
        return Response(
            "<error>Could not generate the public feed.</error>",
            status=500,
            mimetype="application/xml",
        )

    etag = hashlib.sha1(feed_xml).hexdigest()
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304)

    logger.info(
        "feed.generated",
        extra={"route": request.path, "filters": filters, "count": len(items)},
    )

    resp = Response(feed_xml, mimetype=RSS_MIMETYPE)
    resp.headers["Cache-Control"] = "public, max-age=300"
    resp.headers["ETag"] = etag
    return resp


def _build_page_context(tag: str | None) -> Dict[str, Any]:
    filters = normalise_public_feed_filters(
        tag=tag,
        days=request.args.get("days"),
    )
    items = get_public_feed_items(filters)
    rss_query = {k: str(v) for k, v in filters.items()}
    rss_url = url_for("public_feed.public_feed", _external=True, **rss_query)
    metadata = get_public_feed_metadata(
        filters=filters,
        page_url=request.url,
        rss_url=rss_url,
    )
    subscription_links = get_public_feed_subscription_links(rss_url)
    schema_json = build_public_feed_schema(metadata, items)

    logger.info(
        "feed.rendered",
        extra={"route": request.path, "filters": filters, "count": len(items)},
    )
    logger.info(
        "feed.page.view",
        extra={"route": request.path, "tag": filters.get("tag"), "count": len(items)},
    )

    return {
        "filters": filters,
        "items": items,
        "metadata": metadata,
        "subscription_links": subscription_links,
        "schema_json": schema_json,
        "rss_url": rss_url,
    }


@bp.route("/public")
@cache.cached(timeout=300, query_string=True)
def public_feed_page():
    """Human-friendly landing page for the main public feed."""
    try:
        context = _build_page_context(tag=None)
    except FeedIndexBuildingError:
        raise
    response = make_response(render_template("feeds/feed_page.html", **context))
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@bp.route("/tag/<tag>")
@cache.cached(timeout=300, query_string=True)
def tag_feed_page(tag: str):
    """Landing page for tag-specific feeds."""
    try:
        context = _build_page_context(tag=tag)
    except FeedIndexBuildingError:
        raise
    response = make_response(render_template("feeds/feed_page.html", **context))
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@bp.errorhandler(FeedIndexBuildingError)
def handle_bucket_index_building(error: FeedIndexBuildingError) -> Response:
    bucket_slug = None
    if request.view_args:
        bucket_slug = request.view_args.get("bucket_slug")
    feed_label = f"bucket feed '{bucket_slug}'" if bucket_slug else "bucket feed"
    return _render_index_building_response(
        error, feed_label=feed_label, bucket_slug=bucket_slug
    )


@public_bp.errorhandler(FeedIndexBuildingError)
def handle_public_index_building(error: FeedIndexBuildingError) -> Response:
    tag = request.args.get("tag")
    feed_label = "public feed"
    if tag:
        feed_label = f"public feed for tag '{tag}'"
    return _render_index_building_response(error, feed_label=feed_label)
