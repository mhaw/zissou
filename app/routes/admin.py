import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.auth import require_roles
from app.services import (
    buckets as buckets_service,
    items as items_service,
    storage,
    tasks as tasks_service,
)
from app.services.items import FirestoreError
from app.services.storage import StorageError
from app.services.tts import VOICE_PROFILES
from app.services.tasks import (
    ATTENTION_STATUSES,
    IN_PROGRESS_STATUSES,
    RETRYABLE_STATUSES,
    STALE_THRESHOLDS,
    STATUS_FILTER_OPTIONS,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")
logger = logging.getLogger(__name__)


@bp.before_request
def enforce_admin_role():
    return require_roles("admin")


def _parse_list_params():
    sort = request.args.get("sort", "-createdAt")
    after = request.args.get("after")
    limit = int(request.args.get("limit", 50))
    status = (request.args.get("status") or "").strip()
    return {"sort": sort, "after": after, "limit": limit, "status": status}


def _normalize_datetime(value):
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _build_task_health(tasks):
    now = datetime.now(timezone.utc)
    health = {}
    stale_by_status: dict[str, int] = {}

    for task in tasks:
        updated_at = _normalize_datetime(task.updatedAt or task.createdAt)
        age = None
        is_stale = False
        threshold = STALE_THRESHOLDS.get(task.status, timedelta(hours=1))

        if updated_at:
            age = now - updated_at
            if task.status in ATTENTION_STATUSES and age > threshold:
                is_stale = True
                stale_by_status[task.status] = stale_by_status.get(task.status, 0) + 1

        health[task.id] = {
            "updated_at": updated_at,
            "age": age,
            "is_stale": is_stale,
            "threshold": threshold,
        }

    return health, stale_by_status


@bp.route("/")
def index():
    params = _parse_list_params()
    status_filter = params["status"] or None

    tasks, next_cursor = tasks_service.list_tasks(
        sort=params["sort"],
        after=params["after"],
        limit=params["limit"],
        status=status_filter,
    )

    status_counts = tasks_service.get_status_counts()
    recent_activity = tasks_service.get_recent_activity(hours=24)

    task_health, stale_by_status = _build_task_health(tasks)
    stale_total = sum(stale_by_status.values())

    queued_total = status_counts.get("QUEUED", 0)
    in_progress_total = sum(status_counts.get(name, 0) for name in IN_PROGRESS_STATUSES)
    failed_total = status_counts.get("FAILED", 0)
    completed_total = status_counts.get("COMPLETED", 0)

    recent_counts = recent_activity.get("counts", {})
    recent_completed = recent_counts.get("COMPLETED", 0)
    recent_failed = recent_counts.get("FAILED", 0)
    recent_queued = recent_counts.get("QUEUED", 0)

    return render_template(
        "admin/index.html",
        tasks=tasks,
        params=params,
        next_cursor=next_cursor,
        status_counts=status_counts,
        stale_by_status=stale_by_status,
        stale_total=stale_total,
        task_health=task_health,
        status_options=STATUS_FILTER_OPTIONS,
        retryable_statuses=RETRYABLE_STATUSES,
        queued_total=queued_total,
        in_progress_total=in_progress_total,
        failed_total=failed_total,
        completed_total=completed_total,
        recent_completed=recent_completed,
        recent_failed=recent_failed,
        recent_queued=recent_queued,
    )


@bp.route("/bulk_import", methods=["GET", "POST"])
def bulk_import():
    all_buckets = buckets_service.list_buckets()
    if request.method == "POST":
        urls_text = request.form.get("urls_text", "")
        voice = request.form.get("voice")
        bucket_id = request.form.get("bucket_id")

        urls = [url.strip() for url in urls_text.splitlines() if url.strip()]

        if not urls:
            flash("No URLs provided for bulk import.", "error")
            return redirect(url_for("admin.bulk_import"))

        queued_count = 0
        failed_count = 0
        for url in urls:
            try:
                tasks_service.create_task(url, voice=voice, bucket_id=bucket_id)
                queued_count += 1
            except Exception:
                logger.exception("Error queuing URL via bulk import: %s", url)
                failed_count += 1

        if queued_count > 0:
            flash(f"Successfully queued {queued_count} URLs for processing.", "info")
        if failed_count > 0:
            flash(
                f"Failed to queue {failed_count} URLs. See logs for details.", "error"
            )

        return redirect(url_for("admin.bulk_import"))

    return render_template(
        "admin/bulk_import.html", voice_profiles=VOICE_PROFILES, buckets=all_buckets
    )


@bp.route("/retry/<task_id>", methods=["POST"])
def retry_processing(task_id):
    task = tasks_service.get_task(task_id)
    if not task:
        flash("Task not found.", "error")
        return redirect(url_for("admin.index"))

    try:
        tasks_service.retry_task(task)
        flash(f"Re-queued task {task.id} for URL: {task.sourceUrl}", "info")
    except Exception as exc:
        logger.exception("Error retrying task %s", task_id)
        flash(f"Error re-queuing task: {exc}", "error")

    return redirect(url_for("admin.index"))


@bp.route("/items/<item_id>/delete", methods=["POST"])
def delete_item(item_id: str):
    redirect_target = request.form.get("return_to") or url_for("admin.index")
    if not redirect_target.startswith("/"):
        redirect_target = url_for("admin.index")

    item = items_service.get_item(item_id)
    if not item:
        flash("Article not found or already deleted.", "warning")
        return redirect(redirect_target)

    audio_blob = None
    if getattr(item, "audioUrl", None):
        audio_blob = storage.extract_blob_name(item.audioUrl)

    try:
        items_service.delete_item(item_id)
    except FirestoreError as exc:
        logger.exception("Failed to delete item %s: %s", item_id, exc)
        flash("Unable to delete the article. Please try again later.", "error")
        return redirect(redirect_target)

    warnings: list[str] = []

    try:
        tasks_service.detach_item_from_tasks(item_id)
    except FirestoreError as exc:
        logger.warning(
            "Item %s deleted but failed to detach from related tasks: %s", item_id, exc
        )
        warnings.append(
            "Article removed, but some task references could not be updated."
        )

    if audio_blob:
        try:
            storage.delete_blob(audio_blob)
        except StorageError as exc:
            logger.warning(
                "Item %s deleted but failed to delete audio blob %s: %s",
                item_id,
                audio_blob,
                exc,
            )
            warnings.append(
                "Article deleted; the audio file is still present in storage."
            )

    if warnings:
        flash(" ".join(warnings), "warning")
    else:
        flash("Article deleted successfully.", "info")

    return redirect(redirect_target)
