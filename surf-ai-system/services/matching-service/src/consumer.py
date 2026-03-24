import json
import time
from datetime import datetime, timezone
from typing import Any

import boto3

from shared.utils.logger import get_logger
from shared.utils.metrics import MetricsRegistry
from shared.utils.pipeline_store import PipelineStore
from shared.utils.worker_safety import (
    GracefulShutdown,
    WorkerLeaseGuard,
    WorkerRuntimeStats,
    get_receive_count,
    send_to_dlq,
    worker_instance_id,
)
from src.config import MatchingConfig
from src.db import MatchWriteResult, MatchesDB, normalize_embeddings
from src.matcher import MatchAttempt, MatchResult, Matcher

logger = get_logger("matching-consumer")
WORKER_TYPE = "matching-service"


class PermanentMessageError(Exception):
    pass


class MatchingConsumer:
    def __init__(
        self,
        config: MatchingConfig,
        matcher: Matcher,
        matches_db: MatchesDB,
        pipeline_store: PipelineStore,
        metrics: MetricsRegistry,
    ):
        self.config = config
        self.matcher = matcher
        self.matches_db = matches_db
        self.pipeline_store = pipeline_store
        self.metrics = metrics
        self.sqs_client = boto3.client("sqs", region_name=config.aws_region)
        self.shutdown = GracefulShutdown(logger=logger, worker_name=WORKER_TYPE)
        self.leader_id = worker_instance_id(WORKER_TYPE)
        self.runtime_stats = WorkerRuntimeStats(WORKER_TYPE)
        self.lease_guard = WorkerLeaseGuard(
            pipeline_store=self.pipeline_store,
            worker_type=WORKER_TYPE,
            leader_id=self.leader_id,
            ttl_seconds=self.config.worker_lease_ttl_seconds,
            metadata={"queue_url": self.config.input_sqs_url},
            logger=logger,
        )

    def run_forever(self) -> None:
        while not self.shutdown.should_stop():
            try:
                if self.lease_guard.lease_lost():
                    logger.warning("Matching worker lease lost; waiting before retrying leadership")
                    self.shutdown.wait(2)
                    continue

                if not self.lease_guard.ensure_acquired():
                    self.shutdown.wait(2)
                    continue

                response = self.sqs_client.receive_message(
                    QueueUrl=self.config.input_sqs_url,
                    MaxNumberOfMessages=self.config.max_messages,
                    WaitTimeSeconds=self.config.long_poll_seconds,
                    AttributeNames=["All"],
                )
                self.metrics.increment("matching.poll")
                messages = response.get("Messages", [])
                if not messages:
                    self.shutdown.wait(self.config.empty_queue_sleep_seconds)
                    continue

                for message in messages:
                    self._handle_message(message)
            except Exception as exc:
                self.metrics.increment("matching.poll_error")
                logger.error("SQS polling failed: %s", exc, exc_info=True)
                self.shutdown.wait(self.config.error_backoff_seconds)
        try:
            self.lease_guard.release()
        except Exception as exc:
            logger.warning("Failed to release worker lease: %s", exc)
        logger.info("Matching worker metrics snapshot: %s", self.runtime_stats.snapshot())

    def _handle_message(self, message: dict[str, Any]) -> None:
        receipt_handle = message["ReceiptHandle"]
        raw_body = message.get("Body", "")
        receive_count = get_receive_count(message)
        payload: dict[str, Any] | None = None
        logger.info("Message received")
        self.metrics.increment("matching.message_received")

        try:
            payload = self._parse_payload(raw_body)
            job_type = self._job_type(payload)
            logger.info("Processing %s for track_id=%s", job_type, payload.get("track_id"))

            if job_type == "track_embedding_match":
                job_key = self._batch_job_key(payload)
                if not self.pipeline_store.try_start_job(
                    job_type="track_embedding_match",
                    job_key=job_key,
                    job_id=payload.get("job_id") or message.get("MessageId"),
                    payload=payload,
                ):
                    logger.info("Skipping duplicate matching job job_key=%s", job_key)
                    self._record_metric("worker.matching-service.duplicates")
                    self._delete_message(receipt_handle)
                    return
                try:
                    self._process_track_message(payload)
                    self.pipeline_store.finish_job(job_key=job_key, status="completed")
                except Exception as exc:
                    self.pipeline_store.finish_job(
                        job_key=job_key,
                        status="failed",
                        error_message=str(exc),
                    )
                    raise
            elif job_type == "backfill_user_matches":
                self._process_backfill_user_job(payload)
            elif job_type == "rematch_pool_tracks":
                self._process_pool_rematch_job(payload)
            else:
                raise PermanentMessageError(f"unsupported job_type: {job_type}")

            self._delete_message(receipt_handle)
            self.runtime_stats.record_processed()
            self._record_metric("worker.matching-service.messages_processed")
        except PermanentMessageError as exc:
            self.metrics.increment("matching.permanent_error")
            logger.error("Dropping malformed message: %s", exc)
            self.runtime_stats.record_failure()
            self._record_metric("worker.matching-service.failures")
            sent_to_dlq = send_to_dlq(
                sqs_client=self.sqs_client,
                dlq_url=self.config.dlq_sqs_url,
                worker_type=WORKER_TYPE,
                message=message,
                payload=payload,
                reason="permanent_error",
                error_message=str(exc),
            )
            if sent_to_dlq:
                self.runtime_stats.record_dead_letter()
                self._record_metric("worker.matching-service.dead_lettered")
            self._delete_message(receipt_handle)
        except Exception as exc:
            self.metrics.increment("matching.processing_error")
            logger.error("Message processing failed: %s", exc, exc_info=True)
            self.runtime_stats.record_failure()
            self._record_metric("worker.matching-service.failures")
            if receive_count >= self.config.max_receive_count:
                sent_to_dlq = send_to_dlq(
                    sqs_client=self.sqs_client,
                    dlq_url=self.config.dlq_sqs_url,
                    worker_type=WORKER_TYPE,
                    message=message,
                    payload=payload,
                    reason="max_receive_count_exceeded",
                    error_message=str(exc),
                )
                if sent_to_dlq:
                    self.runtime_stats.record_dead_letter()
                    self._record_metric("worker.matching-service.dead_lettered")
                logger.error(
                    "Dead-lettering matching message receive_count=%s sent_to_dlq=%s",
                    receive_count,
                    sent_to_dlq,
                )
                self._delete_message(receipt_handle)
            else:
                self.runtime_stats.record_retry()
                self._record_metric("worker.matching-service.retries")
                logger.warning("Matching message will be retried receive_count=%s", receive_count)
        finally:
            self._log_metrics_if_needed()

    def _parse_payload(self, raw_body: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise PermanentMessageError(f"invalid JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise PermanentMessageError("message body must be a JSON object")
        return payload

    def _job_type(self, payload: dict[str, Any]) -> str:
        return str(payload.get("job_type") or "track_embedding_match")

    def _process_track_message(self, payload: dict[str, Any]) -> None:
        if not payload.get("track_id"):
            raise PermanentMessageError("missing track_id")
        if (
            payload.get("track_embedding") is None
            and payload.get("face_embedding") is None
            and payload.get("embedding") is None
            and payload.get("embeddings") is None
        ):
            raise PermanentMessageError("missing embedding payload")

        logger.info(
            "Matching starts for track_id=%s pool_id=%s video_id=%s",
            payload.get("track_id"),
            payload.get("pool_id"),
            payload.get("video_id") or payload.get("source_video_id"),
        )
        self._match_and_persist(payload)

    def _process_backfill_user_job(self, payload: dict[str, Any]) -> None:
        pool_id = payload.get("pool_id")
        target_user_id = payload.get("user_id")
        if not pool_id:
            raise PermanentMessageError("missing pool_id for backfill")
        if not target_user_id:
            raise PermanentMessageError("missing user_id for backfill")

        candidate_users = self._get_candidate_users_for_backfill(payload)
        if not any(user["user_id"] == target_user_id for user in candidate_users):
            logger.info("Skipping backfill for missing user_id=%s pool_id=%s", target_user_id, pool_id)
            return
        job_key = self._batch_job_key(payload)
        if not self.pipeline_store.try_start_job(
            job_type="backfill_user_matches",
            job_key=job_key,
            job_id=payload.get("job_id"),
            payload=payload,
        ):
            logger.info("Skipping duplicate backfill batch job_key=%s", job_key)
            return

        try:
            track_embeddings, next_cursor = self.pipeline_store.list_pool_track_embeddings(
                pool_id,
                limit=self._batch_size(payload),
                cursor_created_at=payload.get("cursor_created_at"),
                cursor_id=payload.get("cursor_id"),
            )
            if not track_embeddings:
                self.pipeline_store.finish_job(job_key=job_key, status="completed")
                return

            logger.info(
                "Starting backfill for pool_id=%s user_id=%s job_key=%s tracks_in_batch=%s",
                pool_id,
                target_user_id,
                job_key,
                len(track_embeddings),
            )
            outcome_counts = {"matched": 0, "no_match": 0, "duplicate": 0, "skipped": 0}

            for track_embedding in track_embeddings:
                logger.info(
                    "Backfill processing track_id=%s pool_id=%s user_id=%s",
                    track_embedding["track_id"],
                    pool_id,
                    target_user_id,
                )
                outcome = self._match_and_persist(
                    self._track_payload_from_store(track_embedding),
                    candidate_users=candidate_users,
                    expected_user_id=target_user_id,
                )
                outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
                logger.info(
                    "Backfill processed track_id=%s pool_id=%s user_id=%s outcome=%s",
                    track_embedding["track_id"],
                    pool_id,
                    target_user_id,
                    outcome,
                )

            logger.info(
                "Completed backfill for pool_id=%s user_id=%s job_key=%s tracks_processed=%s matches_created=%s duplicates=%s no_match=%s skipped=%s",
                pool_id,
                target_user_id,
                job_key,
                len(track_embeddings),
                outcome_counts.get("matched", 0),
                outcome_counts.get("duplicate", 0),
                outcome_counts.get("no_match", 0),
                outcome_counts.get("skipped", 0),
            )

            if next_cursor is not None:
                self._enqueue_follow_up(payload, next_cursor)
            self.pipeline_store.finish_job(job_key=job_key, status="completed")
        except Exception as exc:
            self.pipeline_store.finish_job(
                job_key=job_key,
                status="failed",
                error_message=str(exc),
            )
            raise

    def _process_pool_rematch_job(self, payload: dict[str, Any]) -> None:
        pool_id = payload.get("pool_id")
        if not pool_id:
            raise PermanentMessageError("missing pool_id for rematch")

        candidate_users = self.matcher.users_db.get_all_users(pool_id=pool_id)
        if not candidate_users:
            logger.info("Skipping rematch for pool_id=%s because no users are available", pool_id)
            return
        job_key = self._batch_job_key(payload)
        if not self.pipeline_store.try_start_job(
            job_type="rematch_pool_tracks",
            job_key=job_key,
            job_id=payload.get("job_id"),
            payload=payload,
        ):
            logger.info("Skipping duplicate rematch batch job_key=%s", job_key)
            return

        try:
            track_embeddings, next_cursor = self.pipeline_store.list_pool_track_embeddings(
                pool_id,
                limit=self._batch_size(payload),
                cursor_created_at=payload.get("cursor_created_at"),
                cursor_id=payload.get("cursor_id"),
            )
            if not track_embeddings:
                self.pipeline_store.finish_job(job_key=job_key, status="completed")
                return

            logger.info(
                "Starting pool rematch for pool_id=%s tracks_in_batch=%s",
                pool_id,
                len(track_embeddings),
            )
            outcome_counts = {"matched": 0, "no_match": 0, "duplicate": 0, "skipped": 0}

            for track_embedding in track_embeddings:
                outcome = self._match_and_persist(
                    self._track_payload_from_store(track_embedding),
                    candidate_users=candidate_users,
                )
                outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

            logger.info(
                "Completed pool rematch for pool_id=%s tracks_processed=%s matches_created=%s duplicates=%s no_match=%s skipped=%s",
                pool_id,
                len(track_embeddings),
                outcome_counts.get("matched", 0),
                outcome_counts.get("duplicate", 0),
                outcome_counts.get("no_match", 0),
                outcome_counts.get("skipped", 0),
            )

            if next_cursor is not None:
                self._enqueue_follow_up(payload, next_cursor)
            self.pipeline_store.finish_job(job_key=job_key, status="completed")
        except Exception as exc:
            self.pipeline_store.finish_job(
                job_key=job_key,
                status="failed",
                error_message=str(exc),
            )
            raise

    def _track_payload_from_store(self, track_embedding: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_type": "track_embedding_match",
            "track_id": track_embedding["track_id"],
            "camera_id": track_embedding.get("camera_id"),
            "pool_id": track_embedding.get("pool_id"),
            "video_id": track_embedding.get("video_id"),
            "source_video_id": track_embedding.get("video_id"),
            "source_video_s3": track_embedding.get("source_video_s3"),
            "keyframe_s3": track_embedding.get("keyframe_s3"),
            "start_time": track_embedding.get("start_time"),
            "end_time": track_embedding.get("end_time"),
            "video_embedding_id": track_embedding.get("video_embedding_id"),
            "track_embedding": track_embedding.get("embedding"),
            "face_embedding": track_embedding.get("embedding"),
            "frames_count": track_embedding.get("frames_count"),
            "num_faces_detected": track_embedding.get("frames_count"),
            "num_embeddings": 1,
            "quality_avg": track_embedding.get("quality_avg"),
            "avg_quality": track_embedding.get("quality_avg"),
            "consistency": track_embedding.get("consistency"),
            "aggregation_method": track_embedding.get("aggregation_method"),
        }

    def _get_candidate_users_for_backfill(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        pool_id = payload.get("pool_id")
        target_user_id = payload.get("user_id")
        users = self.matcher.users_db.get_all_users(pool_id=pool_id)
        override_embedding = payload.get("user_embedding")
        if override_embedding is None:
            return users

        normalized = normalize_embeddings(override_embedding)
        if normalized.size == 0:
            raise PermanentMessageError("backfill user embedding could not be normalized")

        updated_users = []
        for user in users:
            if user["user_id"] != target_user_id:
                updated_users.append(user)
                continue

            updated_user = dict(user)
            updated_user["embeddings"] = normalized
            updated_user["embedding_ids"] = [
                str(payload.get("user_embedding_id") or "uploaded")
            ] * len(normalized)
            updated_users.append(updated_user)
        return updated_users

    def _batch_size(self, payload: dict[str, Any]) -> int:
        raw_value = payload.get("batch_size")
        try:
            return max(1, int(raw_value)) if raw_value is not None else self.config.backfill_batch_size
        except (TypeError, ValueError):
            return self.config.backfill_batch_size

    def _enqueue_follow_up(
        self,
        payload: dict[str, Any],
        next_cursor: dict[str, str],
    ) -> None:
        follow_up = dict(payload)
        follow_up.update(next_cursor)
        self.sqs_client.send_message(
            QueueUrl=self.config.input_sqs_url,
            MessageBody=json.dumps(follow_up),
        )

    def _match_and_persist(
        self,
        payload: dict[str, Any],
        *,
        candidate_users: list[dict[str, Any]] | None = None,
        expected_user_id: str | None = None,
    ) -> str:
        self._record_metric("matching.total_tracks_processed")
        try:
            match_attempt = self.matcher.evaluate_match(payload, candidate_users=candidate_users)
        except ValueError as exc:
            logger.info("Skipping track_id=%s: %s", payload.get("track_id"), exc)
            self._record_match_attempt_diagnostics(
                payload=payload,
                match_attempt=None,
                persist_status="skipped",
                fallback_reason=self._validation_reason_from_message(str(exc)),
                fallback_explanation=str(exc),
            )
            return "skipped"

        match_result = match_attempt.match_result
        if match_result is None:
            self._record_metric("matching.no_match")
            if match_attempt.decision.decision_reason == "min_similarity":
                self._record_metric("matching.rejected.low_similarity")
            if match_attempt.decision.decision_reason == "min_margin":
                self._record_metric("matching.rejected.low_margin")
            logger.info(
                "No match for track_id=%s reason=%s explanation=%s",
                payload.get("track_id"),
                match_attempt.decision.decision_reason,
                match_attempt.decision.explanation,
            )
            self._record_match_attempt_diagnostics(
                payload=payload,
                match_attempt=match_attempt,
                persist_status="no_match",
            )
            return "no_match"

        if expected_user_id is not None and match_result.user_id != expected_user_id:
            logger.info(
                "Backfill track_id=%s best user_id=%s did not match requested user_id=%s",
                match_result.track_id,
                match_result.user_id,
                expected_user_id,
            )
            self._record_match_attempt_diagnostics(
                payload=payload,
                match_attempt=match_attempt,
                persist_status="skipped_target_mismatch",
            )
            return "skipped"

        write_result = self._persist_match(match_result)
        if write_result.status == "duplicate":
            self._record_metric("matching.duplicate_match")
            logger.info(
                "Duplicate match skipped for track_id=%s user_id=%s",
                match_result.track_id,
                match_result.user_id,
            )
            self._record_match_attempt_diagnostics(
                payload=payload,
                match_attempt=match_attempt,
                persist_status=write_result.status,
            )
            return "duplicate"
        if write_result.status == "retained_existing":
            self._record_metric("matching.track_match_stable")
            logger.info(
                "Stable existing match retained for track_id=%s existing_user_id=%s attempted_user_id=%s score_delta=%s required_improvement=%s",
                match_result.track_id,
                write_result.existing_user_id,
                match_result.user_id,
                "n/a" if write_result.score_delta is None else f"{write_result.score_delta:.4f}",
                "n/a" if write_result.required_improvement is None else f"{write_result.required_improvement:.4f}",
            )
            self._record_match_attempt_diagnostics(
                payload=payload,
                match_attempt=match_attempt,
                persist_status=write_result.status,
                write_result=write_result,
            )
            return "duplicate"

        self._publish_match(match_result, payload)
        self._record_metric("matching.match_persisted")
        self._record_metric("matching.matches_created")
        if write_result.status == "reassigned":
            self._record_metric("matching.track_match_reassigned")
        logger.info(
            "Match found for track_id=%s user_id=%s "
            "(similarity=%.4f second_best_similarity=%s margin=%s threshold=%.4f margin_threshold=%.4f distance=%.4f mean=%.4f max=%.4f std=%.4f "
            "embeddings_used=%d evidence_count=%d confidence=%.4f decision=%s persist_status=%s)",
            match_result.track_id,
            match_result.user_id,
            match_result.score,
            "n/a" if match_result.second_best_score is None else f"{match_result.second_best_score:.4f}",
            "n/a" if match_result.score_margin is None else f"{match_result.score_margin:.4f}",
            match_result.threshold_used,
            match_result.margin_threshold_used,
            match_result.min_distance,
            match_result.mean_distance,
            match_result.max_distance,
            match_result.distance_std,
            match_result.embeddings_used,
            match_result.track_evidence_count,
            match_result.confidence,
            "accepted",
            write_result.status,
        )
        self._record_match_attempt_diagnostics(
            payload=payload,
            match_attempt=match_attempt,
            persist_status=write_result.status,
            write_result=write_result,
        )
        return "matched"

    def _persist_match(self, match_result: MatchResult) -> MatchWriteResult:
        return self.matches_db.add_match(
            {
                "user_id": match_result.user_id,
                "pool_id": match_result.pool_id,
                "track_id": match_result.track_id,
                "camera_id": match_result.camera_id,
                "video_id": match_result.video_id,
                "source_video_s3": match_result.source_video_s3,
                "timestamp": match_result.timestamp,
                "keyframe": match_result.keyframe,
                "keyframe_s3": match_result.keyframe_s3,
                "score": match_result.score,
                "confidence": match_result.confidence,
                "distance": match_result.min_distance,
                "embeddings_used": match_result.embeddings_used,
                "distance_mean": match_result.mean_distance,
                "distance_std": match_result.distance_std,
                "distance_max": match_result.max_distance,
                "best_similarity": match_result.score,
                "second_best_score": match_result.second_best_score,
                "second_best_similarity": match_result.second_best_score,
                "margin": match_result.score_margin,
                "score_margin": match_result.score_margin,
                "threshold_used": match_result.threshold_used,
                "margin_threshold_used": match_result.margin_threshold_used,
                "decision_reason": match_result.decision_reason,
                "decision_explanation": match_result.decision_explanation,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            significant_improvement_margin=max(
                float(match_result.margin_threshold_used),
                0.03,
            ),
        )

    def _publish_match(
        self,
        match_result: MatchResult,
        payload: dict[str, Any],
    ) -> None:
        outbound = {
            "user_id": match_result.user_id,
            "pool_id": match_result.pool_id,
            "track_id": match_result.track_id,
            "camera_id": match_result.camera_id,
            "video_id": match_result.video_id,
            "source_video_s3": match_result.source_video_s3,
            "timestamp": match_result.timestamp,
            "keyframe": match_result.keyframe,
            "keyframe_s3": match_result.keyframe_s3,
            "confidence": match_result.confidence,
            "score": match_result.score,
            "distance": match_result.min_distance,
            "distance_mean": match_result.mean_distance,
            "distance_std": match_result.distance_std,
            "distance_max": match_result.max_distance,
            "embeddings_used": match_result.embeddings_used,
            "best_similarity": match_result.score,
            "second_best_score": match_result.second_best_score,
            "second_best_similarity": match_result.second_best_score,
            "margin": match_result.score_margin,
            "score_margin": match_result.score_margin,
            "threshold_used": match_result.threshold_used,
            "margin_threshold_used": match_result.margin_threshold_used,
            "decision_reason": match_result.decision_reason,
            "decision_explanation": match_result.decision_explanation,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if payload.get("start_time"):
            outbound["start_time"] = payload["start_time"]
        if payload.get("end_time"):
            outbound["end_time"] = payload["end_time"]

        body = json.dumps(outbound)

        if self.config.output_sqs_url:
            self.sqs_client.send_message(
                QueueUrl=self.config.output_sqs_url,
                MessageBody=body,
            )

        if self.config.clipper_output_sqs_url:
            self.sqs_client.send_message(
                QueueUrl=self.config.clipper_output_sqs_url,
                MessageBody=body,
            )

    def _delete_message(self, receipt_handle: str) -> None:
        self.sqs_client.delete_message(
            QueueUrl=self.config.input_sqs_url,
            ReceiptHandle=receipt_handle,
        )

    def _log_metrics_if_needed(self) -> None:
        snapshot = self.metrics.snapshot()
        processed = snapshot.get("matching.message_received", 0)
        if processed and processed % self.config.metrics_log_interval == 0:
            logger.info(
                "Matching metrics snapshot: %s runtime=%s",
                snapshot,
                self.runtime_stats.snapshot(),
            )

    def _record_metric(self, name: str, value: int = 1) -> None:
        self.metrics.increment(name, value)
        self.pipeline_store.increment_metric(name, value)

    def _finalize_video_if_resolved(self, video_id: str | None) -> None:
        if not video_id:
            return

        video = self.pipeline_store.get_video(video_id)
        if not video or video.get("status") == "failed":
            return

        diagnostics = video.get("diagnostics") or {}
        frame_data = diagnostics.get("frame_processor") or {}
        expected_tracks = int(frame_data.get("output_tracks", 0) or 0)
        if expected_tracks <= 0:
            if frame_data.get("completed_at"):
                self.pipeline_store.update_video_status(video_id, "completed")
            return

        embedding_tracks = (diagnostics.get("embedding_service") or {}).get("tracks") or {}
        matching_tracks = (diagnostics.get("matching_service") or {}).get("tracks") or {}
        rejected_tracks = sum(
            1 for item in embedding_tracks.values() if item.get("status") == "rejected"
        )
        resolved_tracks = len(matching_tracks) + rejected_tracks
        if resolved_tracks >= expected_tracks:
            self.pipeline_store.update_video_status(video_id, "completed")

    def _batch_job_key(self, payload: dict[str, Any]) -> str:
        job_type = self._job_type(payload)
        base_key = payload.get("idempotency_key") or payload.get("job_id")
        if not base_key:
            if job_type == "backfill_user_matches":
                base_key = (
                    f"backfill:{payload.get('pool_id')}:{payload.get('user_id')}:"
                    f"{payload.get('user_embedding_id') or 'latest'}"
                )
            elif job_type == "rematch_pool_tracks":
                base_key = f"rematch:{payload.get('pool_id')}"
            else:
                base_key = f"{job_type}:{payload.get('track_id') or payload.get('video_embedding_id')}"
        cursor_created_at = payload.get("cursor_created_at") or "root"
        cursor_id = payload.get("cursor_id") or "root"
        return f"{base_key}:{cursor_created_at}:{cursor_id}"

    def _record_match_attempt_diagnostics(
        self,
        *,
        payload: dict[str, Any],
        match_attempt: MatchAttempt | None,
        persist_status: str,
        write_result: MatchWriteResult | None = None,
        fallback_reason: str | None = None,
        fallback_explanation: str | None = None,
    ) -> None:
        video_id = payload.get("video_id") or payload.get("source_video_id")
        track_id = payload.get("track_id")
        if not video_id or not track_id:
            return

        decision = None if match_attempt is None else match_attempt.decision
        frames_count = self._coerce_int(
            payload.get("frames_count"),
            fallback=self._coerce_int(payload.get("num_faces_detected"), fallback=0),
        )
        embeddings_count = self._coerce_int(
            payload.get("embeddings_created"),
            fallback=self._coerce_int(payload.get("num_embeddings"), fallback=0),
        )
        reason = fallback_reason if decision is None else (decision.decision_reason or "accepted")
        explanation = fallback_explanation if decision is None else decision.explanation
        existing_video = self.pipeline_store.get_video(video_id)
        matching_data = ((existing_video or {}).get("diagnostics") or {}).get("matching_service") or {}
        started_at = matching_data.get("started_at") or datetime.now(timezone.utc).isoformat()
        completed_at = datetime.now(timezone.utc).isoformat()
        processing_seconds = None
        try:
            processing_seconds = round(
                max(
                    (
                        datetime.fromisoformat(completed_at)
                        - datetime.fromisoformat(started_at)
                    ).total_seconds(),
                    0.0,
                ),
                3,
            )
        except ValueError:
            processing_seconds = None
        patch = {
            "matching_service": {
                "started_at": started_at,
                "completed_at": completed_at,
                "processing_seconds": processing_seconds,
                "last_processed_at": datetime.now(timezone.utc).isoformat(),
                "tracks": {
                    str(track_id): {
                        "track_id": str(track_id),
                        "video_id": str(video_id),
                        "best_user_id": None if match_attempt is None else match_attempt.best_user_id,
                        "best_similarity": None if decision is None else decision.best_similarity,
                        "second_best_similarity": None if decision is None else decision.second_best_similarity,
                        "margin": None if decision is None else decision.margin,
                        "threshold_used": None if decision is None else decision.threshold_used,
                        "margin_threshold_used": None if decision is None else decision.margin_threshold_used,
                        "decision": "no_match" if decision is None else decision.final_verdict,
                        "decision_reason": reason,
                        "decision_explanation": explanation,
                        "frames_count": frames_count,
                        "embeddings_count": embeddings_count,
                        "persist_status": persist_status,
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                        "existing_user_id": None if write_result is None else write_result.existing_user_id,
                        "score_delta": None if write_result is None else write_result.score_delta,
                        "required_improvement": None if write_result is None else write_result.required_improvement,
                    }
                },
            }
        }
        try:
            self.pipeline_store.update_video_diagnostics(video_id, patch)
            self._finalize_video_if_resolved(video_id)
        except Exception as exc:
            logger.warning(
                "Failed to store matching diagnostics for video_id=%s track_id=%s: %s",
                video_id,
                track_id,
                exc,
            )

    def _validation_reason_from_message(self, message: str) -> str:
        normalized = (message or "").strip().lower()
        if "minimum supported frames" in normalized:
            return "min_frames_per_track"
        if "consistency below minimum threshold" in normalized:
            return "track_consistency"
        return "validation_error"

    def _coerce_int(self, value: Any, *, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(fallback)
