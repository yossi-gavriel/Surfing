from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any


class GracefulShutdown:
    def __init__(self, *, logger, worker_name: str):
        self.logger = logger
        self.worker_name = worker_name
        self._event = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._handle_signal)
            except ValueError:
                # Signal handlers can only be installed from the main thread.
                continue

    def _handle_signal(self, signum, frame) -> None:
        _ = frame
        if not self._event.is_set():
            self.logger.info("%s received signal %s, draining current work", self.worker_name, signum)
        self._event.set()

    def should_stop(self) -> bool:
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        return self._event.wait(seconds)


class WorkerRuntimeStats:
    def __init__(self, worker_type: str):
        self.worker_type = worker_type
        self.started_at = time.monotonic()
        self.processed = 0
        self.failures = 0
        self.retries = 0
        self.dead_lettered = 0

    def record_processed(self) -> None:
        self.processed += 1

    def record_failure(self) -> None:
        self.failures += 1

    def record_retry(self) -> None:
        self.retries += 1

    def record_dead_letter(self) -> None:
        self.dead_lettered += 1

    def snapshot(self) -> dict[str, float | int]:
        elapsed = max(time.monotonic() - self.started_at, 1e-6)
        return {
            "worker_type": self.worker_type,
            "messages_processed": self.processed,
            "failures": self.failures,
            "retries": self.retries,
            "dead_lettered": self.dead_lettered,
            "messages_per_second": round(self.processed / elapsed, 4),
            "uptime_seconds": round(elapsed, 3),
        }


class WorkerLeaseGuard:
    def __init__(
        self,
        *,
        pipeline_store,
        worker_type: str,
        leader_id: str,
        ttl_seconds: int,
        logger,
        metadata: dict[str, Any] | None = None,
        refresh_interval_seconds: float | None = None,
    ):
        self.pipeline_store = pipeline_store
        self.worker_type = worker_type
        self.leader_id = leader_id
        self.ttl_seconds = max(int(ttl_seconds), 1)
        self.logger = logger
        self.metadata = metadata
        self.refresh_interval_seconds = refresh_interval_seconds or max(
            1.0,
            min(float(self.ttl_seconds) / 3.0, max(float(self.ttl_seconds) - 1.0, 1.0)),
        )
        self._heartbeat_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lease_lost_event = threading.Event()
        self._lease_held = threading.Event()
        self._thread_lock = threading.Lock()

    def ensure_acquired(self) -> bool:
        if self._lease_held.is_set() and not self._lease_lost_event.is_set():
            self._ensure_heartbeat_thread()
            return True

        acquired = self.pipeline_store.try_acquire_worker_lease(
            worker_type=self.worker_type,
            leader_id=self.leader_id,
            ttl_seconds=self.ttl_seconds,
            metadata=self.metadata,
        )
        if not acquired:
            return False

        self._lease_lost_event.clear()
        self._lease_held.set()
        self._ensure_heartbeat_thread()
        return True

    def lease_lost(self) -> bool:
        return self._lease_lost_event.is_set()

    def release(self) -> None:
        self._stop_event.set()
        if self._lease_held.is_set():
            self.pipeline_store.release_worker_lease(
                worker_type=self.worker_type,
                leader_id=self.leader_id,
            )
        self._lease_held.clear()

    def _ensure_heartbeat_thread(self) -> None:
        with self._thread_lock:
            if self._heartbeat_thread and self._heartbeat_thread.is_alive():
                return
            if self._stop_event.is_set():
                return
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"{self.worker_type}-lease-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(self.refresh_interval_seconds):
            if not self._lease_held.is_set():
                continue
            try:
                renewed = self.pipeline_store.try_acquire_worker_lease(
                    worker_type=self.worker_type,
                    leader_id=self.leader_id,
                    ttl_seconds=self.ttl_seconds,
                    metadata=self.metadata,
                )
            except Exception as exc:
                renewed = False
                self.logger.error(
                    "%s lease heartbeat failed: %s",
                    self.worker_type,
                    exc,
                    exc_info=True,
                )

            if renewed:
                continue

            self._lease_held.clear()
            self._lease_lost_event.set()
            self.logger.error(
                "%s lost its worker lease; stopping queue consumption to avoid split-brain workers",
                self.worker_type,
            )
            return


def worker_instance_id(worker_type: str) -> str:
    hostname = socket.gethostname()
    pid = os.getpid()
    return f"{worker_type}:{hostname}:{pid}"


def get_receive_count(message: dict[str, Any]) -> int:
    try:
        return int((message.get("Attributes") or {}).get("ApproximateReceiveCount", "1"))
    except (TypeError, ValueError):
        return 1


def send_to_dlq(
    *,
    sqs_client,
    dlq_url: str | None,
    worker_type: str,
    message: dict[str, Any],
    payload: dict[str, Any] | None,
    reason: str,
    error_message: str,
) -> bool:
    if not dlq_url:
        return False

    raw_body = message.get("Body")
    dlq_payload = {
        "worker_type": worker_type,
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "error_message": error_message,
        "receive_count": get_receive_count(message),
        "message_id": message.get("MessageId"),
        "attributes": message.get("Attributes") or {},
        "payload": payload,
        "raw_body": raw_body,
    }
    sqs_client.send_message(
        QueueUrl=dlq_url,
        MessageBody=json.dumps(dlq_payload),
    )
    return True
