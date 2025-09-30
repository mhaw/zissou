import logging
import random
from urllib.parse import urlencode, urlparse
from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
    jsonify,
)
from app.auth import auth_required
from app.extensions import cache
from app.models.smart_bucket import SmartBucketRule
from app.services import (
    items as items_service,
    buckets as buckets_service,
    tasks as tasks_service,
    readwise as readwise_service,
    users as users_service,
    smart_buckets as smart_buckets_service,
)

from app.services.tts import VOICE_PROFILES
from app.services.items import FirestoreError
from app.utils.rate_limits import submission_rate_limiter


bp = Blueprint("main", __name__)
logger = logging.getLogger(__name__)

ALLOWED_URL_SCHEMES = {"http", "https"}
MAX_URL_LENGTH = 2048
MAX_TAG_LENGTH = 64
MAX_TAGS_PER_ITEM = 25


def _parse_list_params():
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "-createdAt")
    bucket = request.args.get("bucket")
    duration = request.args.get("duration")
    after = request.args.get("after")
    limit = int(request.args.get("limit", 25))

    raw_tags = request.args.getlist("tags")
    if not raw_tags:
        legacy_tag = request.args.get("tag")
        if legacy_tag:
            raw_tags = [legacy_tag]

    cleaned_tags = []
    seen = set()
    for tag in raw_tags:
        tag_name = tag.strip()
        if not tag_name or tag_name in seen:
            continue
        cleaned_tags.append(tag_name)
        seen.add(tag_name)

    return {
        "q": q,
        "sort": sort,
        "bucket": bucket,
        "duration": duration,
        "after": after,
        "limit": limit,
        "tags": cleaned_tags,
    }


def _build_next_url(params: dict, next_cursor: str | None) -> str | None:
    if not next_cursor:
        return None

    query_items: list[tuple[str, str]] = []
    for key, value in params.items():
        if key == "after" or value in (None, "", ()):
            continue
        if key == "tags":
            for tag in value or []:
                if tag:
                    query_items.append(("tags", tag))
        elif key == "include_archived":
            if value is True:
                query_items.append(("include_archived", "true"))
        elif key == "include_read":
            if value is True:
                query_items.append(("include_read", "true"))
        else:
            query_items.append((key, str(value)))
    query_items.append(("after", next_cursor))
    query_string = urlencode(query_items, doseq=True)
    base_url = request.base_url
    return f"{base_url}?{query_string}" if query_string else base_url


def _should_skip_cache() -> bool:
    if request.method != "GET":
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accepts = request.accept_mimetypes
    if accepts and accepts.accept_json and not accepts.accept_html:
        return True
    if getattr(g, "user", None):
        return True
    if request.authorization:
        return True
    return False


def _invalidate_page_cache() -> None:
    cache.delete_memoized(index)
    cache.delete_memoized(bucket_items)


def _request_wants_json() -> bool:
    """Returns True if the current request prefers a JSON response."""
    accepts = request.accept_mimetypes
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if not wants_json and accepts:
        wants_json = accepts.accept_json and not accepts.accept_html
    return wants_json


def _load_item_listing(params: dict, archived_status: str = "unarchived") -> dict:
    """Fetches items and associated metadata for browse-style views."""
    user_id = g.user["uid"] if g.user else None
    selected_tags = tuple(params.get("tags", ()))
    items, next_cursor = items_service.list_items(
        user_id=user_id,
        search_query=params.get("q"),
        sort_by=params.get("sort") or "newest",
        tags=list(selected_tags),
        bucket_slug=params.get("bucket"),
        cursor=params.get("after"),
        limit=int(params.get("limit") or 25),
        include_archived=bool(params.get("include_archived")),
        include_read=bool(params.get("include_read")),
    )
    all_buckets = buckets_service.list_buckets()
    bucket_lookup = {
        bucket.id: bucket for bucket in all_buckets if getattr(bucket, "id", None)
    }
    all_tags = items_service.get_all_unique_tags()
    next_url = _build_next_url(params, next_cursor)
    return {
        "items": items,
        "next_cursor": next_cursor,
        "all_buckets": all_buckets,
        "bucket_lookup": bucket_lookup,
        "all_tags": all_tags,
        "selected_tags": selected_tags,
        "next_url": next_url,
    }


def _sync_item_buckets(item_id: str, bucket_ids: list[str]):
    """Persists bucket assignments, refreshes lookup, and clears caches."""
    items_service.update_item_buckets(item_id, bucket_ids)
    item = items_service.get_item(item_id)
    if not item:
        raise LookupError(f"Item {item_id} not found after updating buckets.")
    lookup = {
        bucket.id: bucket
        for bucket in buckets_service.list_buckets()
        if getattr(bucket, "id", None)
    }
    _invalidate_page_cache()
    return item, lookup


def _client_rate_limit_key(action: str) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.remote_addr or "unknown"
    return f"{action}:{client_ip}"


def _enforce_submission_rate_limit(action: str) -> tuple[bool, float]:
    rate_key = _client_rate_limit_key(action)
    allowed, retry_after = submission_rate_limiter.allow(rate_key)
    if not allowed:
        logger.warning("Rate limit triggered for %s (%s)", action, rate_key)
    return allowed, retry_after


def _is_valid_source_url(candidate: str) -> bool:
    if not candidate:
        return False
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        return False
    return bool(parsed.netloc)


@bp.route("/", methods=["GET"])
@auth_required
@cache.cached(query_string=True, unless=_should_skip_cache)
def index():
    """Lists all items."""
    params = _parse_list_params()
    listing = _load_item_listing(params)
    recent_buckets = buckets_service.list_recent_buckets(limit=4)

    if _request_wants_json():
        items_html = render_template(
            "partials/_item_cards.html",
            items=listing["items"],
            bucket_lookup=listing["bucket_lookup"],
        )
        return jsonify(
            {
                "items_html": items_html,
                "next_url": listing["next_url"],
                "items_count": len(listing["items"]),
            }
        )

    return render_template(
        "index.html",
        items=listing["items"],
        buckets=listing["all_buckets"],
        recent_buckets=recent_buckets,
        bucket_lookup=listing["bucket_lookup"],
        params=params,
        selected_tags=listing["selected_tags"],
        next_cursor=listing["next_cursor"],
        next_url=listing["next_url"],
        all_tags=listing["all_tags"],
        include_read=params.get("include_read"),
    )


@bp.route("/new", methods=("GET", "POST"))
@auth_required
def new_item():
    """Handles submission of a new article."""
    all_buckets = buckets_service.list_buckets()
    prefill_url = request.args.get("url", "").strip()
    if request.method == "POST":
        url = (request.form.get("url") or "").strip()
        voice = request.form.get("voice")
        bucket_id = request.form.get("bucket_id")  # Get selected bucket ID
        prefill_url = url or prefill_url

        if not url:
            flash("URL is required.", "error")
            return redirect(url_for("main.new_item", url=prefill_url))
        if len(url) > MAX_URL_LENGTH:
            flash(
                f"URL cannot exceed {MAX_URL_LENGTH} characters.",
                "error",
            )
            return redirect(url_for("main.new_item", url=url))
        if not _is_valid_source_url(url):
            flash("Please enter a valid HTTP or HTTPS URL.", "error")
            return redirect(url_for("main.new_item", url=url))

        allowed, retry_after = _enforce_submission_rate_limit("new-item")
        if not allowed:
            wait_seconds = int(retry_after) + 1
            flash(
                f"Too many submissions right now. Please wait {wait_seconds} seconds and try again.",
                "error",
            )
            return redirect(url_for("main.new_item", url=url))

        try:
            task_id = tasks_service.submit_task(
                url, voice=voice, bucket_id=bucket_id, user=g.user
            )
            return redirect(url_for("main.progress_page", task_id=task_id))
        except (FirestoreError, ValueError) as e:
            logger.error(f"Failed to create processing task for url {url}: {e}")
            flash("Error starting article processing. Please try again later.", "error")
            return redirect(url_for("main.new_item", url=url))

    bookmarklet_target = url_for("main.new_item", _external=True)
    bookmarklet_js = (
        "javascript:(()=>{window.open('"
        + bookmarklet_target
        + "?url='+encodeURIComponent(location.href),'_blank','noopener');})();"
    )

    suggested_voice = random.choice(list(VOICE_PROFILES.keys()))
    return render_template(
        "new_item.html",
        voice_profiles=VOICE_PROFILES,
        buckets=all_buckets,
        prefill_url=prefill_url,
        bookmarklet_js=bookmarklet_js,
        suggested_voice=suggested_voice,
    )  # Pass buckets to template


@bp.route("/import/readwise", methods=("GET", "POST"))
@auth_required
def import_readwise():
    all_buckets = buckets_service.list_buckets()
    shared_url = ""
    share_title = None
    articles: list[readwise_service.ReadwiseArticle] = []
    default_voice = random.choice(list(VOICE_PROFILES.keys()))
    default_bucket = None

    if request.method == "POST":
        shared_url = (request.form.get("shared_url") or "").strip()
        selected_urls = request.form.getlist("article_urls")
        default_voice = request.form.get("voice") or None
        default_bucket = request.form.get("bucket_id") or None

        if not shared_url:
            flash("Provide a Readwise shared link to import.", "error")
            return redirect(url_for("main.import_readwise"))
        if len(shared_url) > MAX_URL_LENGTH:
            flash(
                f"Shared link cannot exceed {MAX_URL_LENGTH} characters.",
                "error",
            )
            return redirect(url_for("main.import_readwise"))
        if not _is_valid_source_url(shared_url):
            flash("Please provide a valid Readwise shared link.", "error")
            return redirect(url_for("main.import_readwise"))

        if not selected_urls:
            flash("Select at least one article to queue.", "error")
            return redirect(
                url_for(
                    "main.import_readwise",
                    shared_url=shared_url,
                    voice=default_voice,
                    bucket_id=default_bucket,
                )
            )

        allowed, retry_after = _enforce_submission_rate_limit("readwise-import")
        if not allowed:
            wait_seconds = int(retry_after) + 1
            flash(
                f"Too many Readwise imports in a short period. Please retry in {wait_seconds} seconds.",
                "error",
            )
            return redirect(
                url_for(
                    "main.import_readwise",
                    shared_url=shared_url,
                    voice=default_voice,
                    bucket_id=default_bucket,
                )
            )

        queued = 0
        failures: list[str] = []
        for raw_article_url in selected_urls:
            article_url = (raw_article_url or "").strip()
            if not article_url:
                failures.append("(missing URL)")
                continue
            if len(article_url) > MAX_URL_LENGTH or not _is_valid_source_url(
                article_url
            ):
                failures.append(article_url)
                continue
            try:
                tasks_service.submit_task(
                    article_url,
                    voice=default_voice or None,
                    bucket_id=default_bucket or None,
                    user=g.user,
                )
                queued += 1
            except Exception:  # pragma: no cover - defensive guard
                logger.exception("Failed to queue Readwise article %s", article_url)
                failures.append(article_url)

        if queued:
            flash(f"Queued {queued} article(s) from Readwise.", "info")
        if failures:
            preview = ", ".join(failures[:5])
            remaining = len(failures) - 5
            if remaining > 0:
                preview = f"{preview}, … (+{remaining} more)"
            flash(
                f"Failed to queue the following article(s): {preview}",
                "error",
            )

        if queued and not failures:
            return redirect(url_for("admin.index"))

        return redirect(
            url_for(
                "main.import_readwise",
                shared_url=shared_url,
                voice=default_voice,
                bucket_id=default_bucket,
            )
        )

    shared_url = (request.args.get("shared_url") or shared_url).strip()
    default_voice = request.args.get("voice") or default_voice
    default_bucket = request.args.get("bucket_id") or default_bucket

    if shared_url:
        try:
            payload = readwise_service.fetch_shared_view(shared_url)
            articles = payload.get("articles", [])
            share_title = payload.get("title")
        except readwise_service.ReadwiseImportError as exc:
            flash(str(exc), "error")
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Unexpected error loading Readwise shared view: %s", exc)
            flash(
                "Unable to load the shared view. Please check the link and try again.",
                "error",
            )

    return render_template(
        "import_readwise.html",
        shared_url=shared_url,
        share_title=share_title,
        readwise_articles=articles,
        voice_profiles=VOICE_PROFILES,
        buckets=all_buckets,
        selected_voice=default_voice,
        selected_bucket=default_bucket,
    )


@bp.route("/progress/<task_id>")
def progress_page(task_id):
    return render_template("progress.html", task_id=task_id)


@bp.route("/status/<task_id>")
def get_task_status(task_id):
    try:
        task = tasks_service.get_task(task_id)
        if not task:
            return jsonify({"status": "NOT_FOUND"}), 404

        status_data = {
            "status": task.status,
            "item_id": task.item_id,
            "error": task.error,
        }
        return jsonify(status_data)
    except FirestoreError as e:
        logger.error(f"Failed to get task status for {task_id}: {e}")
        return (
            jsonify({"status": "ERROR", "error": "Failed to retrieve task status."}),
            500,
        )


@bp.route("/api/items/<item_id>/tags", methods=["POST"])
@auth_required
def update_item_tags_api(item_id):
    payload = request.get_json(silent=True) or {}
    raw_tags = payload.get("tags", [])
    if not isinstance(raw_tags, list):
        return (
            jsonify(
                {"error": "invalid_tags", "message": "Tags must be provided as a list."}
            ),
            400,
        )

    cleaned_tags: list[str] = []
    seen: set[str] = set()
    for tag in raw_tags:
        if not isinstance(tag, str):
            continue
        trimmed = tag.strip()
        if not trimmed:
            continue
        if len(trimmed) > MAX_TAG_LENGTH:
            return (
                jsonify(
                    {
                        "error": "tag_too_long",
                        "message": f"Tags cannot exceed {MAX_TAG_LENGTH} characters.",
                    }
                ),
                400,
            )
        if trimmed in seen:
            continue
        cleaned_tags.append(trimmed)
        seen.add(trimmed)

    if len(cleaned_tags) > MAX_TAGS_PER_ITEM:
        return (
            jsonify(
                {
                    "error": "too_many_tags",
                    "message": f"Items can have at most {MAX_TAGS_PER_ITEM} tags.",
                }
            ),
            400,
        )

    try:
        items_service.update_item_tags(item_id, cleaned_tags)
        item = items_service.get_item(item_id)
        if not item:
            return (
                jsonify({"error": "not_found", "message": "Item no longer exists."}),
                404,
            )
        available_tags = items_service.get_all_unique_tags()
    except FirestoreError as exc:
        logger.error("Error updating tags for item %s: %s", item_id, exc)
        return jsonify({"error": "update_failed", "message": str(exc)}), 500
    except Exception:
        logger.exception("Unexpected error updating tags for item %s", item_id)
        return (
            jsonify(
                {
                    "error": "unexpected_error",
                    "message": "Unexpected error updating tags.",
                }
            ),
            500,
        )

    _invalidate_page_cache()
    tag_summary_html = render_template("partials/_tag_summary.html", tags=item.tags)

    return jsonify(
        {
            "item_id": item_id,
            "tags": item.tags or [],
            "available_tags": available_tags,
            "tag_summary_html": tag_summary_html,
        }
    )


@bp.route("/api/items/<item_id>/buckets", methods=["POST"])
@auth_required
def update_item_buckets_api(item_id):
    payload = request.get_json(silent=True) or {}
    raw_bucket_ids = payload.get("bucket_ids", [])
    if not isinstance(raw_bucket_ids, list):
        return (
            jsonify(
                {"error": "invalid_buckets", "message": "bucket_ids must be a list."}
            ),
            400,
        )

    cleaned_ids: list[str] = []
    seen_ids: set[str] = set()
    for bucket_id in raw_bucket_ids:
        if not isinstance(bucket_id, str):
            continue
        trimmed = bucket_id.strip()
        if trimmed and trimmed not in seen_ids:
            cleaned_ids.append(trimmed)
            seen_ids.add(trimmed)

    try:
        item, lookup = _sync_item_buckets(item_id, cleaned_ids)
    except LookupError:
        return (
            jsonify({"error": "not_found", "message": "Item no longer exists."}),
            404,
        )
    except FirestoreError as exc:
        logger.error("Error updating buckets for item %s: %s", item_id, exc)
        return jsonify({"error": "update_failed", "message": str(exc)}), 500
    except Exception:
        logger.exception("Unexpected error updating buckets for item %s", item_id)
        return (
            jsonify(
                {
                    "error": "unexpected_error",
                    "message": "Unexpected error updating buckets.",
                }
            ),
            500,
        )

    bucket_names: list[str] = []
    if item.buckets:
        for identifier in item.buckets:
            bucket = lookup.get(identifier)
            bucket_names.append(bucket.name if bucket else identifier)
    bucket_summary_html = render_template(
        "partials/_bucket_summary.html",
        bucket_ids=item.buckets or [],
        bucket_lookup=lookup,
    )

    return jsonify(
        {
            "item_id": item_id,
            "bucket_ids": item.buckets or [],
            "bucket_names": bucket_names,
            "bucket_summary_html": bucket_summary_html,
        }
    )


@bp.route("/items/<item_id>", methods=("GET", "POST"))
@auth_required
def item_detail(item_id):
    item = items_service.get_item(item_id)
    if not item:
        flash("Item not found.")
        return redirect(url_for("main.index"))

    if request.method == "POST":
        if "tags" in request.form:
            raw_tags = request.form.getlist("tags")
            if not raw_tags:
                raw_tags = [request.form.get("tags", "")]
            tags = [tag.strip() for tag in raw_tags if tag and tag.strip()]
            try:
                items_service.update_item_tags(item_id, tags)
                _invalidate_page_cache()
                flash("Item tags updated successfully!")
            except Exception:
                logger.exception(f"Error updating tags for item_id: {item_id}")
                flash("An unexpected error occurred while updating tags.", "error")
        else:
            selected_buckets = request.form.getlist("bucket_ids")
            try:
                _sync_item_buckets(item_id, selected_buckets)
                flash("Item buckets updated successfully!")
            except LookupError:
                flash("Item no longer exists.", "error")
            except Exception:
                logger.exception(f"Error updating buckets for item_id: {item_id}")
                flash("An unexpected error occurred while updating buckets.", "error")

        return redirect(url_for("main.item_detail", item_id=item_id))

    all_buckets = buckets_service.list_buckets()
    bucket_lookup = {bucket.id: bucket for bucket in all_buckets if bucket.id}
    bucket_options = []
    for bucket in all_buckets:
        if not bucket.id:
            continue
        display_name = bucket.name or bucket.slug or bucket.id
        bucket_options.append({"id": bucket.id, "name": display_name})
    all_tags = items_service.get_all_unique_tags()
    return render_template(
        "item_detail.html",
        item=item,
        buckets=all_buckets,
        bucket_lookup=bucket_lookup,
        bucket_options=bucket_options,
        all_tags=all_tags,
    )


@bp.route("/items/<item_id>/read", methods=["POST"])
@auth_required
def read_item(item_id):
    """Marks an item as read or unread."""
    item = items_service.get_item(item_id)
    if not item:
        flash("Item not found.", "error")
        return redirect(url_for("main.index"))

    try:
        items_service.toggle_read_status(item_id, g.user["uid"])
        _invalidate_page_cache()
        flash(
            f"Item marked as {'read' if item.is_read else 'unread'} successfully.",
            "info",
        )
    except FirestoreError as e:
        logger.error(f"Failed to update read status for item {item_id}: {e}")
        flash("Error updating item. Please try again later.", "error")
    except PermissionError:
        flash("You do not have permission to modify this item.", "error")

    return redirect(request.referrer or url_for("main.index"))


@bp.route("/items/<item_id>/archive", methods=["POST"])
@auth_required
def archive_item(item_id):
    """Archives or unarchives an item."""
    item = items_service.get_item(item_id)
    if not item:
        flash("Item not found.", "error")
        return redirect(url_for("main.index"))

    is_archived = not item.is_archived
    try:
        items_service.update_item_archived_status(item_id, is_archived)
        _invalidate_page_cache()
        flash(
            f"Item {'archived' if is_archived else 'unarchived'} successfully.", "info"
        )
    except FirestoreError as e:
        logger.error(f"Failed to update archived status for item {item_id}: {e}")
        flash("Error updating item. Please try again later.", "error")

    return redirect(request.referrer or url_for("main.index"))


@bp.route("/buckets/<bucket_id>/items")
@cache.cached(query_string=True, unless=_should_skip_cache)
def bucket_items(bucket_id):
    bucket = buckets_service.get_bucket(bucket_id)
    if not bucket:
        flash("Bucket not found.")
        return redirect(url_for("main.list_buckets"))

    params = _parse_list_params()
    params["bucket"] = bucket_id
    listing = _load_item_listing(params)

    if _request_wants_json():
        items_html = render_template(
            "partials/_item_cards.html",
            items=listing["items"],
            bucket_lookup=listing["bucket_lookup"],
        )
        return jsonify(
            {
                "items_html": items_html,
                "next_url": listing["next_url"],
                "items_count": len(listing["items"]),
            }
        )

    return render_template(
        "bucket_items.html",
        bucket=bucket,
        items=listing["items"],
        buckets=listing["all_buckets"],
        bucket_lookup=listing["bucket_lookup"],
        params=params,
        selected_tags=listing["selected_tags"],
        next_cursor=listing["next_cursor"],
        next_url=listing["next_url"],
        all_tags=listing["all_tags"],
    )


@bp.route("/buckets", methods=("GET", "POST"))
def list_buckets():
    if request.method == "POST":
        name = request.form["name"]
        slug = request.form["slug"]
        description = request.form["description"]
        rss_author_name = request.form.get("rss_author_name")
        rss_owner_email = request.form.get("rss_owner_email")
        rss_cover_image_url = request.form.get("rss_cover_image_url")
        itunes_categories_str = request.form.get("itunes_categories")
        itunes_categories = (
            [cat.strip() for cat in itunes_categories_str.split(",") if cat.strip()]
            if itunes_categories_str
            else []
        )

        if not name or not slug:
            flash("Bucket name and slug are required.", "error")
        else:
            try:
                buckets_service.create_bucket(
                    name,
                    slug,
                    description,
                    rss_author_name,
                    rss_owner_email,
                    rss_cover_image_url,
                    itunes_categories,
                )
                flash("Bucket created successfully!")
            except Exception:
                logger.exception(f"Error creating bucket: {name} ({slug})")
                flash(
                    "An unexpected error occurred while creating the bucket.", "error"
                )

        return redirect(url_for("main.list_buckets"))

    all_buckets = buckets_service.list_buckets()
    return render_template("buckets.html", buckets=all_buckets)


@bp.route("/health")
def health_check():
    return "OK", 200


@bp.route("/archived")
def archived_items():
    """Lists all archived items."""
    params = _parse_list_params()
    listing = _load_item_listing(params, archived_status="archived")

    if _request_wants_json():
        items_html = render_template(
            "partials/_item_cards.html",
            items=listing["items"],
            bucket_lookup=listing["bucket_lookup"],
        )
        return jsonify(
            {
                "items_html": items_html,
                "next_url": listing["next_url"],
                "items_count": len(listing["items"]),
            }
        )

    return render_template(
        "archived.html",
        items=listing["items"],
        buckets=listing["all_buckets"],
        bucket_lookup=listing["bucket_lookup"],
        params=params,
        selected_tags=listing["selected_tags"],
        next_cursor=listing["next_cursor"],
        next_url=listing["next_url"],
        all_tags=listing["all_tags"],
    )


@bp.route("/surprise_me", methods=["GET"])
@auth_required
def surprise_me():
    """Redirects to a random unread item."""
    user_id = g.user["uid"]
    item = items_service.get_random_unread_item(user_id)
    if item:
        return redirect(url_for("main.item_detail", item_id=item.id))
    else:
        flash("No unread items found. Why not add some new articles?", "info")
        return redirect(url_for("main.index"))


@bp.route("/dashboard")
@auth_required
def dashboard():
    user = getattr(g, "user", {})
    email = user.get("email") or user.get("uid") or "signed-in user"
    return f"Protected dashboard ready for {email}"


@bp.route("/smart-buckets", methods=["GET", "POST"])
@auth_required
def smart_buckets():
    if request.method == "POST":
        name = request.form.get("name")
        rules = []
        rule_index = 0
        while f"rules[{rule_index}][field]" in request.form:
            field = request.form.get(f"rules[{rule_index}][field]")
            operator = request.form.get(f"rules[{rule_index}][operator]")
            value = request.form.get(f"rules[{rule_index}][value]")
            rules.append(SmartBucketRule(field=field, operator=operator, value=value))
            rule_index += 1

        if name and rules:
            smart_bucket = smart_buckets_service.SmartBucket(name=name, rules=rules)
            smart_buckets_service.create_smart_bucket(smart_bucket)
            flash("Smart bucket created successfully.", "success")
        else:
            flash("Name and at least one rule are required.", "error")

        return redirect(url_for("main.smart_buckets"))

    smart_buckets = smart_buckets_service.list_smart_buckets()
    return render_template("smart_buckets.html", smart_buckets=smart_buckets)


@bp.route("/profile", methods=["GET", "POST"])
@auth_required
def profile():
    user = users_service.get_user(g.user["uid"])
    all_buckets = buckets_service.list_buckets()
    if request.method == "POST":
        default_voice = request.form.get("default_voice")
        default_bucket_id = request.form.get("default_bucket_id")
        users_service.update_user(
            user.id,
            {"default_voice": default_voice, "default_bucket_id": default_bucket_id},
        )
        flash("Your profile has been updated.", "success")
        return redirect(url_for("main.profile"))

    return render_template(
        "profile.html", user=user, voice_profiles=VOICE_PROFILES, buckets=all_buckets
    )


@bp.route("/profile/delete", methods=["POST"])
@auth_required
def delete_account():
    """Permanently deletes the current user's account."""
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        abort(403)

    try:
        user_id = g.user["uid"]
        users_service.delete_user(user_id)
        # The logout route will clear the session cookie
        return jsonify({"status": "ok"}), 200
    except users_service.FirestoreError as e:
        logger.error(f"Failed to delete account for user {g.user['uid']}: {e}")
        return jsonify({"error": "Failed to delete account."}), 500
