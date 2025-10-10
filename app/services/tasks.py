import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Any

from google.cloud import firestore  # type: ignore[attr-defined]
from google.cloud.exceptions import GoogleCloudError
from google.cloud.firestore_v1 import FieldFilter
from google.cloud.tasks_v2 import CloudTasksClient

from app.models.task import Task
from app.services.items import db, FirestoreError


logger = logging.getLogger(__name__)

TASKS_COLLECTION = os.getenv("FIRESTORE_COLLECTION_TASKS", "tasks")

STATUS_LABELS = {
    "QUEUED": "Queued",
    "VALIDATING_INPUT": "Validating input",
    "CHECKING_EXISTING": "Checking existing",
    "PARSING": "Parsing",
    "CONVERTING_AUDIO": "Converting audio",
    "UPLOADING_AUDIO": "Uploading audio",
    "SAVING_ITEM": "Saving item",
    "PROCESSING": "Processing",
    "REQUEUED": "Re-queued",
    "FAILED": "Failed",
    "COMPLETED": "Completed",
}

STATUS_FILTER_OPTIONS = [("", "All statuses")] + [
    (key, label) for key, label in STATUS_LABELS.items()
]
IN_PROGRESS_STATUSES = {
    "VALIDATING_INPUT",
    "CHECKING_EXISTING",
    "PARSING",
    "CONVERTING_AUDIO",
    "UPLOADING_AUDIO",
    "SAVING_ITEM",
    "PROCESSING",
}
ATTENTION_STATUSES = {
    "QUEUED",
    "VALIDATING_INPUT",
    "CHECKING_EXISTING",
    "PARSING",
    "CONVERTING_AUDIO",
    "UPLOADING_AUDIO",
    "SAVING_ITEM",
    "PROCESSING",
}
STALE_THRESHOLDS = {
    "QUEUED": timedelta(minutes=5),
    "VALIDATING_INPUT": timedelta(minutes=5),
    "CHECKING_EXISTING": timedelta(minutes=5),
    "PARSING": timedelta(minutes=10),
    "CONVERTING_AUDIO": timedelta(minutes=30),
    "UPLOADING_AUDIO": timedelta(minutes=10),
    "SAVING_ITEM": timedelta(minutes=10),
    "PROCESSING": timedelta(minutes=20),
}
RETRYABLE_STATUSES = {"FAILED", "QUEUED"}
STATUS_COUNT_DEFAULTS = ["QUEUED", "PROCESSING", "FAILED", "COMPLETED"]
RECENT_ACTIVITY_DEFAULTS = ["COMPLETED", "FAILED", "QUEUED"]
OPEN_TASK_STATUSES = {
    "QUEUED",
    "VALIDATING_INPUT",
    "CHECKING_EXISTING",
    "PARSING",
    "CONVERTING_AUDIO",
    "UPLOADING_AUDIO",
    "SAVING_ITEM",
    "PROCESSING",
}


def _ensure_db_client():
    if db is None:
        raise FirestoreError("Firestore client is not initialized.")


def _run_count(query):
    try:
        count_query = query.count()
        count_results = list(count_query.get())
        if count_results:
            try:
                return count_results[0][0].value
            except (IndexError, TypeError, AttributeError):
                aggregation_result = count_results[0]
                if hasattr(aggregation_result, "value"):
                    return aggregation_result.value
                if hasattr(aggregation_result, "aggregate_fields"):
                    return aggregation_result.aggregate_fields.get("count", 0)
    except (GoogleCloudError, AttributeError):
        logger.debug(
            "Count aggregation not available, falling back to streaming query."
        )
    return sum(1 for _ in query.stream())


def create_cloud_task(task_payload: dict):
    """Creates a new task in Google Cloud Tasks."""
    project = os.getenv("GCP_PROJECT_ID")
    location = os.getenv("CLOUD_TASKS_LOCATION")
    queue = os.getenv("CLOUD_TASKS_QUEUE")
    service_url = os.getenv("SERVICE_URL")
    sa_email = os.getenv("SERVICE_ACCOUNT_EMAIL")

    if not all([project, location, queue, service_url, sa_email]):
        logger.error("Cloud Tasks environment variables not fully configured.")
        raise ValueError("Cloud Tasks environment is not configured.")

    client = CloudTasksClient()
    parent = client.queue_path(project, location, queue)  # type: ignore[arg-type]

    task = {
        "http_request": {
            "http_method": "POST",
            "url": f"{service_url}/tasks/process",
            "headers": {"Content-type": "application/json"},
            "oidc_token": {
                "service_account_email": sa_email,
            },
            "body": json.dumps(task_payload).encode(),
        }
    }

    try:
        response = client.create_task(parent=parent, task=task)  # type: ignore[arg-type]
        logger.info(f"Created Cloud Task: {response.name}")
        return response
    except GoogleCloudError as e:
        logger.exception(f"Error creating Cloud Task: {e}")
        raise


def create_task(
    url: str,
    voice: Optional[str] = None,
    bucket_id: Optional[str] = None,
    user: Any = None,
) -> str:
    """
    Creates a task document in Firestore and, if in a deployed environment,
    enqueues a corresponding task in Google Cloud Tasks.
    In a local dev environment, processes the task synchronously.
    """
    _ensure_db_client()
    try:
        if user:
            default_voice = (
                user.get("default_voice")
                if isinstance(user, dict)
                else getattr(user, "default_voice", None)
            )
            if default_voice:
                voice = default_voice

            default_bucket = (
                user.get("default_bucket_id")
                if isinstance(user, dict)
                else getattr(user, "default_bucket_id", None)
            )
            if default_bucket and not bucket_id:
                bucket_id = default_bucket

        task = Task(sourceUrl=url, voice=voice, bucket_id=bucket_id)
        if user:
            task.userId = (
                user.get("uid")
                if isinstance(user, dict)
                else getattr(user, "uid", None)
            )

        tasks_ref = db.collection(TASKS_COLLECTION)
        try:
            potential_duplicates = (
                tasks_ref.where(filter=FieldFilter("sourceUrl", "==", url))
                .order_by("createdAt", direction=firestore.Query.DESCENDING)
                .limit(5)
                .stream()
            )
        except GoogleCloudError as exc:
            logger.warning("Failed to check for duplicate tasks on %s: %s", url, exc)
            potential_duplicates = []

        for duplicate in potential_duplicates:
            candidate = duplicate.to_dict() or {}
            status = candidate.get("status")
            candidate_voice = candidate.get("voice")
            candidate_bucket = candidate.get("bucket_id")
            if status not in OPEN_TASK_STATUSES:
                continue
            if voice and candidate_voice and candidate_voice != voice:
                continue
            if bucket_id and candidate_bucket and candidate_bucket != bucket_id:
                continue
            logger.info(
                "Reusing active task %s for %s instead of enqueuing duplicate",
                duplicate.id,
                url,
            )
            return duplicate.id

        task_ref = tasks_ref.document()
        task.id = task_ref.id

        # Local development: process synchronously
        if os.getenv("ENV") == "development":
            from app.routes.tasks import process_article_task

            logger.info(f"Processing task {task.id} synchronously in local dev.")
            task.status = "PROCESSING"
            task_ref.set(task.to_dict())
            process_article_task(task.id, url, voice, bucket_id, task.userId)
            return task.id

        # Deployed environment: enqueue to Cloud Tasks
        task.status = "QUEUED"
        task_ref.set(task.to_dict())

        task_payload = {
            "task_id": task.id,
            "url": url,
            "voice": voice,
            "bucket_id": bucket_id,
            "user_id": task.userId,
        }
        create_cloud_task(task_payload)

        return task.id

    except GoogleCloudError as e:
        logger.error(f"Firestore error creating task for url {url}: {e}")
        raise FirestoreError(f"Failed to create task for url {url}.") from e
    except Exception as e:
        logger.exception(f"An unexpected error occurred creating task for url {url}")
        # If task was created in Firestore but failed to enqueue, mark it as failed
        if "task_ref" in locals() and task_ref.id:
            update_task(
                task_ref.id, status="FAILED", error=f"Failed to enqueue task: {e}"
            )
        raise


def submit_task(
    url: str,
    voice: Optional[str] = None,
    bucket_id: Optional[str] = None,
    user: Any = None,
) -> str:
    """Backward-compatible alias for ``create_task``."""

    return create_task(url, voice=voice, bucket_id=bucket_id, user=user)


def retry_task(task: Task) -> str:
    _ensure_db_client()
    if not task.id:
        raise FirestoreError("Cannot retry a task without an id.")

    task_ref = db.collection(TASKS_COLLECTION).document(task.id)
    retry_count = (task.retryCount or 0) + 1
    now = datetime.now(timezone.utc)

    if os.getenv("ENV") == "development":
        from app.routes.tasks import process_article_task

        update_fields = {
            "status": "PROCESSING",
            "updatedAt": now,
            "error": None,
            "errorCode": None,
            "item_id": None,
            "retryCount": retry_count,
        }
        task_ref.update(update_fields)
        process_article_task(
            task.id,
            task.sourceUrl,
            task.voice,
            task.bucket_id,
            task.userId,
        )
        return task.id

    update_fields = {
        "status": "QUEUED",
        "updatedAt": now,
        "error": None,
        "errorCode": None,
        "item_id": None,
        "retryCount": retry_count,
    }
    task_ref.update(update_fields)

    payload = {
        "task_id": task.id,
        "url": task.sourceUrl,
        "voice": task.voice,
        "bucket_id": task.bucket_id,
    }
    create_cloud_task(payload)
    return task.id


def get_task(task_id: str) -> Task | None:
    """Retrieves a task by its ID."""
    _ensure_db_client()
    try:
        task_ref = db.collection(TASKS_COLLECTION).document(task_id)
        doc = task_ref.get()
        if not doc.exists:
            return None
        return Task.from_dict(doc.id, doc.to_dict())
    except GoogleCloudError as e:
        logger.error(f"Firestore error getting task {task_id}: {e}")
        raise FirestoreError(f"Failed to get task {task_id}.") from e


def get_task_by_source_url(source_url: str) -> Task | None:
    """Retrieves the most recent task for a given source URL."""
    _ensure_db_client()
    try:
        tasks_ref = db.collection(TASKS_COLLECTION)
        query = (
            tasks_ref.where(filter=firestore.FieldFilter("sourceUrl", "==", source_url))
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(1)
        )
        docs = list(query.stream())
        if not docs:
            return None
        return _doc_to_task(docs[0])
    except GoogleCloudError as e:
        logger.error(f"Firestore error getting task by source URL {source_url}: {e}")
        raise FirestoreError(f"Failed to get task by source URL {source_url}.") from e


def claim_task_for_processing(task_id: str) -> tuple[str, Task | None]:
    """Atomically transition a queued task to PROCESSING.

    Returns a tuple ``(status, task)`` where status is one of ``"claimed"``,
    ``"duplicate"``, or ``"missing"``.
    """
    _ensure_db_client()
    task_ref = db.collection(TASKS_COLLECTION).document(task_id)

    @firestore.transactional
    def _claim(transaction, ref):
        snapshot = ref.get(transaction=transaction)
        if not snapshot.exists:
            return "missing", None
        task_obj = _doc_to_task(snapshot)
        current_status = task_obj.status
        if current_status != "QUEUED":
            return "duplicate", task_obj
        transaction.update(
            ref,
            {"status": "PROCESSING", "updatedAt": datetime.now(timezone.utc)},
        )
        task_obj.status = "PROCESSING"
        task_obj.updatedAt = datetime.now(timezone.utc)
        return "claimed", task_obj

    try:
        transaction = db.transaction()
        return _claim(transaction, task_ref)
    except GoogleCloudError as exc:
        logger.error("Firestore error claiming task %s: %s", task_id, exc)
        raise FirestoreError(f"Failed to claim task {task_id} for processing.") from exc


def update_task(
    task_id: str,
    status: str,
    item_id: Optional[str] = None,
    error: Optional[str] = None,
    error_code: Optional[str] = None,
):
    """Updates the status and other fields of a task document."""
    _ensure_db_client()
    try:
        task_ref = db.collection(TASKS_COLLECTION).document(task_id)
        update_data = {"status": status, "updatedAt": datetime.now(timezone.utc)}
        if item_id:
            update_data["item_id"] = item_id
        if error:
            update_data["error"] = error
        if error_code:
            update_data["errorCode"] = error_code

        task_ref.update(update_data)
    except GoogleCloudError as e:
        logger.error(f"Firestore error updating task {task_id}: {e}")
        raise FirestoreError(f"Failed to update task {task_id}.") from e


def _doc_to_task(doc) -> Task:
    """Converts a Firestore document to a Task dataclass."""
    return Task.from_dict(doc.id, doc.to_dict())


def _build_index_hint(
    status: str | None,
    search_query: str | None,
    sort_field: str,
    sort_direction,
) -> dict:
    """Construct the composite index definition required for the current query."""
    fields: list[dict[str, str]] = []
    if search_query:
        fields.append({"fieldPath": "sourceUrl", "order": "ASCENDING"})
    if status:
        fields.append({"fieldPath": "status", "order": "ASCENDING"})

    direction = "DESCENDING" if sort_direction == firestore.Query.DESCENDING else "ASCENDING"
    fields.append({"fieldPath": sort_field, "order": direction})

    return {
        "collectionGroup": TASKS_COLLECTION,
        "queryScope": "COLLECTION",
        "fields": fields,
    }


def list_tasks(
    sort: str = "-createdAt",
    after: Optional[str] = None,
    limit: int = 50,
    status: str | None = None,
    search_query: str | None = None,
) -> tuple[list[Task], str | None]:
    """Return tasks sorted by the requested field along with a pagination cursor."""
    _ensure_db_client()
    sort_field = sort.lstrip("-")
    sort_direction = (
        firestore.Query.DESCENDING if sort.startswith("-") else firestore.Query.ASCENDING
    )
    try:
        tasks_ref = db.collection(TASKS_COLLECTION)
        query = tasks_ref
        if status:
            query = query.where(filter=firestore.FieldFilter("status", "==", status))
        if search_query:
            query = query.where(
                filter=firestore.FieldFilter("sourceUrl", "==", search_query)
            )

        query = query.order_by(sort_field, direction=sort_direction)

        if after:
            start_after_doc = tasks_ref.document(after).get()
            if start_after_doc.exists:
                query = query.start_after(start_after_doc)

        docs = query.limit(limit + 1).stream()
        tasks = [_doc_to_task(doc) for doc in docs]

        next_cursor = None
        if len(tasks) > limit:
            next_cursor = tasks[limit].id
            tasks = tasks[:limit]

        return tasks, next_cursor

    except GoogleCloudError as e:
        logger.error(f"Firestore error listing tasks: {e}")
        message = str(e).lower()
        if "index" in message or "indexes" in message:
            hint = _build_index_hint(status, search_query, sort_field, sort_direction)
            logger.error(
                "Composite index required for tasks query: %s",
                json.dumps(hint),
            )
        raise FirestoreError("Failed to list tasks from Firestore.") from e


def query_tasks(status: str, limit: int = 10) -> list[Task]:
    """Queries for tasks with a specific status."""
    _ensure_db_client()
    try:
        tasks_ref = db.collection(TASKS_COLLECTION)
        query = tasks_ref.where(
            filter=firestore.FieldFilter("status", "==", status)
        ).limit(limit)
        docs = query.stream()
        return [_doc_to_task(doc) for doc in docs]
    except GoogleCloudError as e:
        logger.error(f"Firestore error querying tasks with status {status}: {e}")
        raise FirestoreError(f"Failed to query tasks with status {status}.") from e


def get_status_counts(statuses: list[str] | None = None) -> dict[str, int]:
    """Return counts for each status in ``statuses`` (defaults to ``STATUS_COUNT_DEFAULTS``)."""
    _ensure_db_client()
    tasks_ref = db.collection(TASKS_COLLECTION)
    status_list = statuses or STATUS_COUNT_DEFAULTS
    counts: dict[str, int] = {}
    for status_name in status_list:
        query = tasks_ref.where(
            filter=firestore.FieldFilter("status", "==", status_name)
        )
        counts[status_name] = _run_count(query)
    counts["TOTAL"] = sum(counts.values())
    return counts


def get_recent_activity(
    hours: int = 24, statuses: list[str] | None = None
) -> dict[str, object]:
    """Return counts of task outcomes updated within ``hours`` (defaults to ``RECENT_ACTIVITY_DEFAULTS``)."""
    _ensure_db_client()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    tasks_ref = db.collection(TASKS_COLLECTION)
    status_list = statuses or RECENT_ACTIVITY_DEFAULTS
    counts: dict[str, int] = {}
    for status_name in status_list:
        query = tasks_ref.where(
            filter=firestore.FieldFilter("status", "==", status_name)
        ).where(filter=firestore.FieldFilter("updatedAt", ">=", cutoff))
        counts[status_name] = _run_count(query)
    return {"cutoff": cutoff, "counts": counts}


def detach_item_from_tasks(item_id: str) -> int:
    """Remove references to the provided item_id from related tasks."""
    _ensure_db_client()
    tasks_ref = db.collection(TASKS_COLLECTION)
    try:
        docs = list(
            tasks_ref.where(
                filter=firestore.FieldFilter("item_id", "==", item_id)
            ).stream()
        )
        updated = 0
        for doc in docs:
            tasks_ref.document(doc.id).update(
                {"item_id": None, "updatedAt": datetime.now(timezone.utc)}
            )
            updated += 1
        if updated:
            logger.info("Detached item %s from %s task(s)", item_id, updated)
        return updated
    except GoogleCloudError as e:
        logger.error("Firestore error detaching item %s from tasks: %s", item_id, e)
        raise FirestoreError(
            f"Failed to detach item {item_id} from related tasks."
        ) from e
    except Exception as e:  # pragma: no cover - defensive guard
        logger.error("Unexpected error detaching item %s from tasks: %s", item_id, e)
        raise FirestoreError(
            f"An unexpected error occurred while detaching item {item_id} from tasks."
        ) from e
