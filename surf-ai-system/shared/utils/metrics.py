import threading
from typing import Any


class MetricsRegistry:
    def __init__(self):
        self._counters: dict[str, int] = {}
        self._lock = threading.Lock()

    def increment(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + value

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._counters)
