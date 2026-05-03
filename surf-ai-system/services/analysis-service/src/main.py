import json
import os
import signal
import sys
import time
import uuid

import boto3

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

from src.config import AnalysisConfig, config, FAILURE_CODES, MODEL_VERSION, PROCESSING_TIMEOUT_SECONDS
from shared.utils.logger import get_logger
from shared.utils.pipeline_store import PipelineStore
from shared.utils.worker_safety import (
    GracefulShutdown,
    WorkerLeaseGuard,
    WorkerRuntimeStats,
    get_receive_count,
    send_to_dlq,
    worker_instance_id,
)

logger = get_logger("analysis-service")
WORKER_TYPE = "analysis-service"
sqs_client = boto3.client("sqs", region_name=config.aws_region)
s3_client = boto3.client("s3", region_name=config.aws_region)
pipeline_store = PipelineStore(os.environ.get("SQLITE_DB_PATH", "/app/data/surf_ai.db"))

# Lazy-loaded analyzer (Phase 1+)
_analyzer = None


def _get_analyzer():
    global _analyzer
    if _analyzer is None:
        try:
            from src.analyzer import RideAnalyzer
            _analyzer = RideAnalyzer(config, s3_client, logger)
            logger.info("RideAnalyzer initialized successfully, model_version=%s", MODEL_VERSION)
        except Exception as e:
            logger.warning("RideAnalyzer not available (Phase 0 stub mode): %s", e)
            _analyzer = False  # sentinel: tried and failed
    return _analyzer if _analyzer is not False else None


def _record_metric(name: str, value: int = 1) -> None:
    try:
        pipeline_store.increment_metric(f"worker.{WORKER_TYPE}.{name}", value)
    except Exception:
        pass


def _analysis_job_key(msg_body: dict) -> str:
    if msg_body.get("idempotency_key"):
        return str(msg_body["idempotency_key"])
    return f"analysis:{msg_body.get('video_id')}:{msg_body.get('track_id')}:{msg_body.get('start_time', 'unknown')}"


def _create_or_get_analysis_job(msg_body: dict) -> dict | None:
    """Create an analysis_jobs row. Returns the job dict, or None if already completed."""
    job_id = str(uuid.uuid4())
    track_id = msg_body.get("track_id", "")
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    try:
        with pipeline_store.store.connection() as conn:
            existing = conn.execute(
                "SELECT job_id, status, retry_count FROM analysis_jobs WHERE track_id = ?",
                (track_id,),
            ).fetchone()

            if existing:
                if existing["status"] == "completed":
                    return None  # already done
                # Reset for retry
                conn.execute(
                    """UPDATE analysis_jobs
                       SET status = 'processing', retry_count = retry_count + 1,
                           started_at = ?, updated_at = ?, failure_code = NULL, failure_reason = NULL
                       WHERE track_id = ?""",
                    (now, now, track_id),
                )
                return {
                    "job_id": existing["job_id"],
                    "track_id": track_id,
                    "retry_count": existing["retry_count"] + 1,
                }

            conn.execute(
                """INSERT INTO analysis_jobs
                   (job_id, track_id, video_id, user_id, pool_id, camera_id,
                    status, retry_count, retryable, clip_s3, model_version,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'processing', 0, 1, ?, ?, ?, ?)""",
                (
                    job_id,
                    track_id,
                    msg_body.get("video_id"),
                    msg_body.get("user_id"),
                    msg_body.get("pool_id"),
                    msg_body.get("camera_id"),
                    msg_body.get("clip_s3", ""),
                    MODEL_VERSION,
                    now,
                    now,
                ),
            )
            return {"job_id": job_id, "track_id": track_id, "retry_count": 0}
    except Exception as e:
        logger.error("Failed to create/get analysis job for track_id=%s: %s", track_id, e)
        return {"job_id": job_id, "track_id": track_id, "retry_count": 0}


def _update_job_status(track_id: str, status: str, failure_code: str | None = None,
                       failure_reason: str | None = None, retryable: bool = True,
                       canonical_s3: str | None = None, debug_s3: str | None = None,
                       ride_duration: float | None = None, dominant_direction: str | None = None,
                       ride_score: float | None = None, maneuver_count: int | None = None,
                       analysis_duration_ms: int | None = None) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    completed_at = now if status in ("completed", "partial") else None
    try:
        with pipeline_store.store.connection() as conn:
            conn.execute(
                """UPDATE analysis_jobs
                   SET status = ?, failure_code = ?, failure_reason = ?, retryable = ?,
                       canonical_s3 = ?, debug_s3 = ?,
                       ride_duration_seconds = ?, dominant_direction = ?,
                       ride_score = ?, maneuver_count = ?,
                       analysis_duration_ms = ?,
                       completed_at = ?, updated_at = ?
                   WHERE track_id = ?""",
                (
                    status, failure_code, failure_reason, 1 if retryable else 0,
                    canonical_s3, debug_s3,
                    ride_duration, dominant_direction,
                    ride_score, maneuver_count,
                    analysis_duration_ms,
                    completed_at, now, track_id,
                ),
            )
    except Exception as e:
        logger.error("Failed to update analysis job status for track_id=%s: %s", track_id, e)


def _is_retryable(failure_code: str) -> bool:
    spec = FAILURE_CODES.get(failure_code, {"retryable": True, "max_retries": 3})
    return spec["retryable"]


def _max_retries_for(failure_code: str) -> int:
    spec = FAILURE_CODES.get(failure_code, {"retryable": True, "max_retries": 3})
    return spec["max_retries"]


def _validate_clip_exists(msg_body: dict) -> str | None:
    """HEAD-check the clip on S3. Returns failure_code or None if OK."""
    clip_s3 = msg_body.get("clip_s3", "")
    if not clip_s3:
        return "clip_corrupt"

    try:
        if "://" in clip_s3:
            parts = clip_s3.split("//", 1)[1].split("/", 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ""
        else:
            bucket = config.s3_bucket
            key = clip_s3

        s3_client.head_object(Bucket=bucket, Key=key)
        return None
    except Exception:
        return "clip_download_failed"


def _write_stub_canonical(msg_body: dict) -> str | None:
    """Write a minimal stub canonical JSON to S3 (Phase 0)."""
    track_id = msg_body.get("track_id", "unknown")
    canonical = {
        "$schema": "ride_summary_v1",
        "track_id": track_id,
        "video_id": msg_body.get("video_id"),
        "user_id": msg_body.get("user_id"),
        "pool_id": msg_body.get("pool_id"),
        "model_version": MODEL_VERSION,
        "status": "stub",
        "ride": None,
        "wave": None,
        "maneuvers": [],
        "score": None,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    }

    key = f"analysis/{track_id}/ride_summary.json"
    try:
        s3_client.put_object(
            Bucket=config.s3_bucket,
            Key=key,
            Body=json.dumps(canonical, indent=2),
            ContentType="application/json",
        )
        return f"s3://{config.s3_bucket}/{key}"
    except Exception as e:
        logger.error("[%s] Failed to write stub canonical: %s", track_id, e)
        return None


def process_analysis(msg_body: dict) -> None:
    """Process a single analysis message. Phase 0: stub. Phase 1+: real analysis."""
    track_id = msg_body.get("track_id", "unknown")
    video_id = msg_body.get("video_id", "unknown")
    clip_s3 = msg_body.get("clip_s3", "")
    start_time = time.time()

    log_ctx = (
        f"job_id=pending track_id={track_id} video_id={video_id} "
        f"clip_s3_key={clip_s3} model_version={MODEL_VERSION}"
    )

    logger.info("[%s] stage=receive status=started %s", track_id, log_ctx)

    # Create/get job row
    job = _create_or_get_analysis_job(msg_body)
    if job is None:
        logger.info("[%s] stage=receive status=skipped reason=already_completed", track_id)
        _record_metric("duplicates")
        return

    job_id = job["job_id"]
    retry_count = job["retry_count"]
    log_ctx = (
        f"job_id={job_id} track_id={track_id} video_id={video_id} "
        f"clip_s3_key={clip_s3} model_version={MODEL_VERSION}"
    )

    logger.info("[%s] stage=validate status=started %s", track_id, log_ctx)

    # Validate clip exists on S3
    validation_failure = _validate_clip_exists(msg_body)
    if validation_failure:
        retryable = _is_retryable(validation_failure)
        logger.error(
            "[%s] stage=validate status=failed failure_code=%s retryable=%s %s",
            track_id, validation_failure, retryable, log_ctx,
        )
        _update_job_status(
            track_id, "failed",
            failure_code=validation_failure,
            failure_reason=f"Clip validation failed: {validation_failure}",
            retryable=retryable,
        )
        if not retryable:
            raise _NonRetryableError(validation_failure)
        raise _RetryableError(validation_failure)

    logger.info("[%s] stage=validate status=completed %s", track_id, log_ctx)

    # Try real analysis (Phase 1+)
    analyzer = _get_analyzer()
    if analyzer is not None:
        logger.info("[%s] stage=analysis status=started %s", track_id, log_ctx)
        result = analyzer.analyze(msg_body)
        duration_ms = int((time.time() - start_time) * 1000)

        if result.get("failure_code"):
            failure_code = result["failure_code"]
            retryable = _is_retryable(failure_code)
            logger.error(
                "[%s] stage=analysis status=failed failure_code=%s failure_reason=%s retryable=%s %s",
                track_id, failure_code, result.get("failure_reason", ""), retryable, log_ctx,
            )
            _update_job_status(
                track_id, "failed" if not result.get("canonical_s3") else "partial",
                failure_code=failure_code,
                failure_reason=result.get("failure_reason"),
                retryable=retryable,
                canonical_s3=result.get("canonical_s3"),
                debug_s3=result.get("debug_s3"),
                analysis_duration_ms=duration_ms,
            )
            if not retryable:
                raise _NonRetryableError(failure_code)
            raise _RetryableError(failure_code)

        _update_job_status(
            track_id, "completed",
            canonical_s3=result.get("canonical_s3"),
            debug_s3=result.get("debug_s3"),
            ride_duration=result.get("ride_duration_seconds"),
            dominant_direction=result.get("dominant_direction"),
            ride_score=result.get("ride_score"),
            maneuver_count=result.get("maneuver_count"),
            analysis_duration_ms=duration_ms,
        )
        logger.info(
            "[%s] stage=complete status=completed total_duration_ms=%d final_status=completed %s",
            track_id, duration_ms, log_ctx,
        )
        return

    # Phase 0 stub path: write stub canonical, mark completed
    logger.info("[%s] stage=stub_write status=started %s", track_id, log_ctx)
    canonical_s3 = _write_stub_canonical(msg_body)
    duration_ms = int((time.time() - start_time) * 1000)

    if canonical_s3 is None:
        _update_job_status(
            track_id, "failed",
            failure_code="s3_write_failed",
            failure_reason="Failed to write stub canonical JSON",
            retryable=True,
            analysis_duration_ms=duration_ms,
        )
        raise _RetryableError("s3_write_failed")

    _update_job_status(
        track_id, "completed",
        canonical_s3=canonical_s3,
        analysis_duration_ms=duration_ms,
    )
    logger.info(
        "[%s] stage=complete status=completed total_duration_ms=%d final_status=completed canonical_s3=%s %s",
        track_id, duration_ms, canonical_s3, log_ctx,
    )


class _NonRetryableError(Exception):
    def __init__(self, failure_code: str):
        self.failure_code = failure_code
        super().__init__(failure_code)


class _RetryableError(Exception):
    def __init__(self, failure_code: str):
        self.failure_code = failure_code
        super().__init__(failure_code)


class _TimeoutError(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _TimeoutError("Processing timeout exceeded")


def main():
    logger.info("Analysis service starting, model_version=%s", MODEL_VERSION)
    shutdown = GracefulShutdown(logger=logger, worker_name=WORKER_TYPE)
    leader_id = worker_instance_id(WORKER_TYPE)
    stats = WorkerRuntimeStats(WORKER_TYPE)
    lease_guard = WorkerLeaseGuard(
        pipeline_store=pipeline_store,
        worker_type=WORKER_TYPE,
        leader_id=leader_id,
        ttl_seconds=config.worker_lease_ttl_seconds,
        metadata={"queue_url": config.input_sqs_url},
        logger=logger,
    )

    # Register timeout handler
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _timeout_handler)

    while not shutdown.should_stop():
        try:
            if lease_guard.lease_lost():
                logger.warning("Analysis worker lease lost; waiting before retry")
                shutdown.wait(2)
                continue

            if not lease_guard.ensure_acquired():
                shutdown.wait(2)
                continue

            response = sqs_client.receive_message(
                QueueUrl=config.input_sqs_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                AttributeNames=["All"],
            )

            messages = response.get("Messages", [])
            for message in messages:
                if shutdown.should_stop():
                    break

                receipt_handle = message["ReceiptHandle"]
                receive_count = get_receive_count(message)

                try:
                    body = json.loads(message["Body"])
                except json.JSONDecodeError as exc:
                    logger.error("Invalid analysis message JSON: %s", exc)
                    stats.record_failure()
                    _record_metric("failures")
                    send_to_dlq(
                        sqs_client=sqs_client,
                        dlq_url=config.dlq_sqs_url,
                        worker_type=WORKER_TYPE,
                        message=message,
                        payload=None,
                        reason="invalid_json",
                        error_message=str(exc),
                    )
                    sqs_client.delete_message(QueueUrl=config.input_sqs_url, ReceiptHandle=receipt_handle)
                    continue

                track_id = body.get("track_id", "unknown")

                # Set processing timeout
                if hasattr(signal, "SIGALRM"):
                    signal.alarm(PROCESSING_TIMEOUT_SECONDS)

                try:
                    process_analysis(body)
                    sqs_client.delete_message(QueueUrl=config.input_sqs_url, ReceiptHandle=receipt_handle)
                    stats.record_processed()
                    _record_metric("messages_processed")

                    if stats.processed % config.metrics_log_interval == 0:
                        logger.info("Analysis worker metrics: %s", stats.snapshot())

                except _NonRetryableError as e:
                    logger.warning("[%s] Non-retryable failure: %s — sending to DLQ", track_id, e.failure_code)
                    stats.record_failure()
                    _record_metric("failures")
                    _record_metric(f"failure.{e.failure_code}")
                    send_to_dlq(
                        sqs_client=sqs_client,
                        dlq_url=config.dlq_sqs_url,
                        worker_type=WORKER_TYPE,
                        message=message,
                        payload=body,
                        reason=e.failure_code,
                        error_message=str(e),
                    )
                    sqs_client.delete_message(QueueUrl=config.input_sqs_url, ReceiptHandle=receipt_handle)

                except _RetryableError as e:
                    logger.warning("[%s] Retryable failure: %s (receive_count=%d)", track_id, e.failure_code, receive_count)
                    stats.record_failure()
                    _record_metric("failures")
                    _record_metric(f"failure.{e.failure_code}")
                    max_retries = _max_retries_for(e.failure_code)
                    if receive_count >= max_retries:
                        send_to_dlq(
                            sqs_client=sqs_client,
                            dlq_url=config.dlq_sqs_url,
                            worker_type=WORKER_TYPE,
                            message=message,
                            payload=body,
                            reason=f"{e.failure_code}_max_retries",
                            error_message=str(e),
                        )
                        sqs_client.delete_message(QueueUrl=config.input_sqs_url, ReceiptHandle=receipt_handle)
                        stats.record_dead_letter()
                        _record_metric("dead_lettered")
                    else:
                        stats.record_retry()
                        _record_metric("retries")

                except _TimeoutError:
                    logger.error("[%s] Processing timeout after %ds", track_id, PROCESSING_TIMEOUT_SECONDS)
                    _update_job_status(track_id, "failed", failure_code="timeout",
                                       failure_reason=f"Exceeded {PROCESSING_TIMEOUT_SECONDS}s", retryable=True)
                    stats.record_failure()
                    _record_metric("failures")
                    _record_metric("failure.timeout")
                    if receive_count >= _max_retries_for("timeout"):
                        send_to_dlq(
                            sqs_client=sqs_client,
                            dlq_url=config.dlq_sqs_url,
                            worker_type=WORKER_TYPE,
                            message=message,
                            payload=body,
                            reason="timeout_max_retries",
                            error_message=f"Timeout after {PROCESSING_TIMEOUT_SECONDS}s",
                        )
                        sqs_client.delete_message(QueueUrl=config.input_sqs_url, ReceiptHandle=receipt_handle)
                        stats.record_dead_letter()
                        _record_metric("dead_lettered")
                    else:
                        stats.record_retry()
                        _record_metric("retries")

                except Exception as e:
                    logger.error("[%s] Unexpected error: %s", track_id, e, exc_info=True)
                    _update_job_status(track_id, "failed", failure_code="internal_error",
                                       failure_reason=str(e)[:500], retryable=True)
                    stats.record_failure()
                    _record_metric("failures")
                    _record_metric("failure.internal_error")
                    if receive_count >= config.max_receive_count:
                        send_to_dlq(
                            sqs_client=sqs_client,
                            dlq_url=config.dlq_sqs_url,
                            worker_type=WORKER_TYPE,
                            message=message,
                            payload=body,
                            reason="max_receive_count_exceeded",
                            error_message=str(e)[:500],
                        )
                        sqs_client.delete_message(QueueUrl=config.input_sqs_url, ReceiptHandle=receipt_handle)
                        stats.record_dead_letter()
                        _record_metric("dead_lettered")
                    else:
                        stats.record_retry()
                        _record_metric("retries")

                finally:
                    if hasattr(signal, "SIGALRM"):
                        signal.alarm(0)

        except Exception as e:
            logger.error("Analysis consumer loop error: %s", e, exc_info=True)
            shutdown.wait(5)

    try:
        lease_guard.release()
    except Exception as exc:
        logger.warning("Failed to release analysis lease: %s", exc)
    logger.info("Analysis worker final metrics: %s", stats.snapshot())


if __name__ == "__main__":
    main()
