import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, status


class SlidingWindowRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(
        self,
        key: str,
        *,
        code: str = "rate_limited",
        message: str = "Too many upload attempts. Please try again later.",
    ) -> None:
        now = time.time()
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] <= now - self.window_seconds:
                bucket.popleft()

            if len(bucket) >= self.max_requests:
                retry_after = max(1, int(self.window_seconds - (now - bucket[0])))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "code": code,
                        "message": message,
                        "retry_after_seconds": retry_after,
                    },
                )

            bucket.append(now)
