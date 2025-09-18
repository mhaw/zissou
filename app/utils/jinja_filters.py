import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

TAG_COLOR_CLASSES = [
    "border-sky-200 bg-sky-50 text-sky-700",
    "border-violet-200 bg-violet-50 text-violet-700",
    "border-emerald-200 bg-emerald-50 text-emerald-700",
    "border-amber-200 bg-amber-50 text-amber-700",
    "border-rose-200 bg-rose-50 text-rose-700",
    "border-teal-200 bg-teal-50 text-teal-700",
    "border-indigo-200 bg-indigo-50 text-indigo-700",
    "border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700",
    "border-lime-200 bg-lime-50 text-lime-700",
    "border-slate-200 bg-slate-100 text-slate-700",
]


def format_duration(seconds):
    if seconds is None:
        return "N/A"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    elif minutes:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"


def format_datetime(dt):
    if dt is None:
        return "N/A"
    # Assuming dt is a datetime object. If it's a Firestore Timestamp, it needs .astimezone() or similar.
    # For simplicity, let's assume it's a Python datetime object.
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        # If dt is naive, assume UTC for comparison
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        # If dt is aware, convert to UTC
        dt = dt.astimezone(timezone.utc)
    diff = now - dt

    if diff < timedelta(minutes=1):
        return "just now"
    elif diff < timedelta(hours=1):
        return f"{int(diff.total_seconds() // 60)} minutes ago"
    elif diff < timedelta(days=1):
        return f"{int(diff.total_seconds() // 3600)} hours ago"
    elif diff < timedelta(days=30):
        return f"{int(diff.total_seconds() // 86400)} days ago"
    else:
        return dt.strftime("%b %d, %Y")


def url_host(url):
    if url is None:
        return "N/A"
    try:
        return urlparse(url).netloc.replace("www.", "")
    except (AttributeError, ValueError):
        return url


def nl2p(text):
    if text is None:
        return ""
    paragraphs = text.split("\n\n")
    html = "".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())
    return html


def tag_color_class(tag: str) -> str:
    if not tag:
        return "border-slate-200 bg-slate-100 text-slate-700"
    digest = hashlib.md5(tag.lower().encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(TAG_COLOR_CLASSES)
    return TAG_COLOR_CLASSES[index]


def merge_dicts(dict1, dict2):
    """
    Merges two dictionaries.

    If a key exists in both dictionaries, the value from the second dictionary
    will be used.
    """
    if isinstance(dict1, dict) and isinstance(dict2, dict):
        merged = dict1.copy()
        merged.update(dict2)
        return merged
    return {}
