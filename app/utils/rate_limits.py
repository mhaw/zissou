import os
import threading
import time
from collections import deque
from typing import Deque, Tuple


class SlidingWindowRateLimiter:
    """Simple in-memory sliding window limiter for per-process throttling."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> Tuple[bool, float]:
        """Return a tuple of (allowed, retry_after_seconds)."""
        if self.max_requests <= 0:
            return True, 0.0

        now = time.monotonic()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            window_start = now - self.window_seconds
            while bucket and bucket[0] < window_start:
                bucket.popleft()

            if len(bucket) >= self.max_requests:
                retry_after = self.window_seconds - (now - bucket[0])
                return False, max(retry_after, 0.0)

            bucket.append(now)
            return True, 0.0


DEFAULT_SUBMISSION_RATE_LIMIT = int(os.getenv("TASK_SUBMISSION_RATE_LIMIT", "25"))
DEFAULT_SUBMISSION_WINDOW_SECONDS = float(
    os.getenv("TASK_SUBMISSION_WINDOW_SECONDS", "60")
)

submission_rate_limiter = SlidingWindowRateLimiter(
    DEFAULT_SUBMISSION_RATE_LIMIT,
    DEFAULT_SUBMISSION_WINDOW_SECONDS,
)
