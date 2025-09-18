from flask import Blueprint, Response, request
import logging
import hashlib
from ..services.feeds import generate_feed_for_bucket

bp = Blueprint("feeds", __name__, url_prefix="/feeds")
logger = logging.getLogger(__name__)


@bp.route("/<bucket_slug>.xml")
def bucket_feed(bucket_slug):
    """Generates a paginated RSS feed for a given bucket with ETag caching."""
    try:
        page = request.args.get("page", 1, type=int)
        # request.base_url gives the URL for the endpoint without the query string
        feed_base_url = request.base_url

        feed_xml = generate_feed_for_bucket(bucket_slug, feed_base_url, page)

        etag = hashlib.sha1(feed_xml).hexdigest()

        if_none_match = request.headers.get("If-None-Match")
        if if_none_match == etag:
            return Response(status=304)

        resp = Response(feed_xml, mimetype="application/rss+xml; charset=utf-8")
        resp.headers["Cache-Control"] = "public, max-age=900"
        resp.headers["ETag"] = etag
        return resp

    except Exception:
        logger.exception(f"Error generating feed for bucket: {bucket_slug}")
        return Response(
            f"<error>Could not generate feed for {bucket_slug}. An unexpected error occurred.</error>",
            status=500,
            mimetype="application/xml",
        )
