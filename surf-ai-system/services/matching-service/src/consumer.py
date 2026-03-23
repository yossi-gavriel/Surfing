import json
import time
from datetime import datetime, timezone
from typing import Any

import boto3

from shared.utils.logger import get_logger
from shared.utils.metrics import MetricsRegistry
from src.config import MatchingConfig
from src.db import MatchesDB
from src.matcher import MatchResult, Matcher

logger = get_logger("matching-consumer")


class PermanentMessageError(Exception):
    pass


class MatchingConsumer:
    def __init__(
        self,
        config: MatchingConfig,
        matcher: Matcher,
        matches_db: MatchesDB,
        metrics: MetricsRegistry,
    ):
        self.config = config
        self.matcher = matcher
        self.matches_db = matches_db
        self.metrics = metrics
        self.sqs_client = boto3.client("sqs", region_name=config.aws_region)

    def run_forever(self) -> None:
        while True:
            try:
                response = self.sqs_client.receive_message(
                    QueueUrl=self.config.input_sqs_url,
                    MaxNumberOfMessages=self.config.max_messages,
                    WaitTimeSeconds=self.config.long_poll_seconds,
                )
                self.metrics.increment("matching.poll")
                messages = response.get("Messages", [])
                if not messages:
                    time.sleep(self.config.empty_queue_sleep_seconds)
                    continue

                for message in messages:
                    self._handle_message(message)
            except KeyboardInterrupt:
                logger.info("Stopping matching consumer")
                break
            except Exception as exc:
                self.metrics.increment("matching.poll_error")
                logger.error("SQS polling failed: %s", exc, exc_info=True)
                time.sleep(self.config.error_backoff_seconds)

    def _handle_message(self, message: dict[str, Any]) -> None:
        receipt_handle = message["ReceiptHandle"]
        raw_body = message.get("Body", "")
        logger.info("Message received")
        self.metrics.increment("matching.message_received")

        try:
            payload = self._parse_payload(raw_body)
            logger.info("Processing started for track_id=%s", payload.get("track_id"))

            try:
                match_result = self.matcher.match(payload)
            except ValueError as exc:
                raise PermanentMessageError(str(exc)) from exc

            if match_result is None:
                self.metrics.increment("matching.no_match")
                logger.info("No match for track_id=%s", payload.get("track_id"))
                self._delete_message(receipt_handle)
                self._log_metrics_if_needed()
                return

            is_new_match = self._persist_match(match_result)
            if not is_new_match:
                self.metrics.increment("matching.duplicate_match")
                logger.info(
                    "Duplicate match skipped for track_id=%s user_id=%s",
                    match_result.track_id,
                    match_result.user_id,
                )
                self._delete_message(receipt_handle)
                self._log_metrics_if_needed()
                return

            self._publish_match(match_result, payload)
            self.metrics.increment("matching.match_persisted")

            logger.info(
                "Match found for track_id=%s user_id=%s "
                "(score=%.4f second_best_score=%s margin=%s distance=%.4f mean=%.4f max=%.4f std=%.4f "
                "embeddings_used=%d evidence_count=%d confidence=%.4f "
                "confidence_breakdown=1-distance decision=%s)",
                match_result.track_id,
                match_result.user_id,
                match_result.score,
                "n/a" if match_result.second_best_score is None else f"{match_result.second_best_score:.4f}",
                "n/a" if match_result.score_margin is None else f"{match_result.score_margin:.4f}",
                match_result.min_distance,
                match_result.mean_distance,
                match_result.max_distance,
                match_result.distance_std,
                match_result.embeddings_used,
                match_result.track_evidence_count,
                match_result.confidence,
                "accepted-force-match" if match_result.force_match_used else "accepted",
            )
            self._delete_message(receipt_handle)
        except PermanentMessageError as exc:
            self.metrics.increment("matching.permanent_error")
            logger.error("Dropping malformed message: %s", exc)
            self._delete_message(receipt_handle)
        except Exception as exc:
            self.metrics.increment("matching.processing_error")
            logger.error("Message processing failed: %s", exc, exc_info=True)
        finally:
            self._log_metrics_if_needed()

    def _parse_payload(self, raw_body: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise PermanentMessageError(f"invalid JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise PermanentMessageError("message body must be a JSON object")

        if not payload.get("track_id"):
            raise PermanentMessageError("missing track_id")

        if (
            payload.get("embedding") is None
            and payload.get("embeddings") is None
            and payload.get("face_embedding") is None
        ):
            raise PermanentMessageError("missing embedding payload")

        return payload

    def _persist_match(self, match_result: MatchResult) -> bool:
        return self.matches_db.add_match(
            {
                "user_id": match_result.user_id,
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
                "second_best_score": match_result.second_best_score,
                "score_margin": match_result.score_margin,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _publish_match(
        self,
        match_result: MatchResult,
        payload: dict[str, Any],
    ) -> None:
        outbound = {
            "user_id": match_result.user_id,
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
            "second_best_score": match_result.second_best_score,
            "score_margin": match_result.score_margin,
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
            logger.info("Matching metrics snapshot: %s", snapshot)
