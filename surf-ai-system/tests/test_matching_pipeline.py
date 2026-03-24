import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATCHING_SERVICE_ROOT = PROJECT_ROOT / "services" / "matching-service"
EMBEDDING_SERVICE_ROOT = PROJECT_ROOT / "services" / "embedding-service"
EMBEDDING_SRC_ROOT = EMBEDDING_SERVICE_ROOT / "src"

os.environ.setdefault("MATCHING_INPUT_SQS_URL", "https://example.com/matching")

for path in (PROJECT_ROOT, MATCHING_SERVICE_ROOT, EMBEDDING_SRC_ROOT):
    resolved = str(path)
    if resolved not in sys.path:
        sys.path.insert(0, resolved)

from aggregator import EmbeddingAggregator
from shared.utils.debug_compare import build_debug_compare_response
from shared.utils.metrics import MetricsRegistry
from shared.utils.system_config import SystemConfigService
from shared.utils.pipeline_store import PipelineStore
from src.consumer import MatchingConsumer
from src.db import MatchesDB, UsersDB
from src.matcher import Matcher


class ConfigStub:
    aws_region = "us-east-1"
    input_sqs_url = "https://example.com/matching"
    output_sqs_url = None
    clipper_output_sqs_url = None
    dlq_sqs_url = "https://example.com/matching-dlq"
    sqlite_db_path = ""
    users_db_path = ""
    matches_db_path = ""
    match_threshold = 0.75
    min_similarity = 0.75
    min_margin = 0.05
    top_k_candidates = 25
    min_track_embeddings = 3
    max_distance_std = 0.08
    min_track_consistency = 0.75
    margin = 0.05
    min_score = 0.6
    max_messages = 10
    backfill_batch_size = 100
    long_poll_seconds = 1
    empty_queue_sleep_seconds = 0.01
    error_backoff_seconds = 0.01
    metrics_log_interval = 1000
    max_receive_count = 5
    worker_lease_ttl_seconds = 60
    allow_single_embedding_debug = False


class FakeSQSClient:
    def __init__(self):
        self.sent_messages = []
        self.deleted_receipts = []

    def send_message(self, QueueUrl, MessageBody):
        self.sent_messages.append({"QueueUrl": QueueUrl, "MessageBody": MessageBody})
        return {"MessageId": str(len(self.sent_messages))}

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.deleted_receipts.append({"QueueUrl": QueueUrl, "ReceiptHandle": ReceiptHandle})


class MatchingPipelineTests(unittest.TestCase):
    def test_pipeline_store_worker_lease_is_exclusive_until_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            store = PipelineStore(db_path)

            acquired_first = store.try_acquire_worker_lease(
                worker_type="matching-service",
                leader_id="leader-a",
                ttl_seconds=30,
                metadata={"slot": "blue"},
            )
            acquired_second = store.try_acquire_worker_lease(
                worker_type="matching-service",
                leader_id="leader-b",
                ttl_seconds=30,
                metadata={"slot": "green"},
            )
            lease = store.get_worker_lease("matching-service")

            self.assertTrue(acquired_first)
            self.assertFalse(acquired_second)
            self.assertIsNotNone(lease)
            assert lease is not None
            self.assertEqual(lease["leader_id"], "leader-a")
            self.assertEqual(lease["metadata"], {"slot": "blue"})

            store.release_worker_lease(worker_type="matching-service", leader_id="leader-a")

            reacquired = store.try_acquire_worker_lease(
                worker_type="matching-service",
                leader_id="leader-b",
                ttl_seconds=30,
                metadata={"slot": "green"},
            )
            self.assertTrue(reacquired)
            self.assertEqual(store.get_worker_lease("matching-service")["leader_id"], "leader-b")

    def test_pipeline_store_job_locks_retry_failed_jobs_but_skip_completed_jobs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            store = PipelineStore(db_path)

            first_start = store.try_start_job(
                job_type="track_embedding_match",
                job_key="matching:video-1:track-1",
                job_id="msg-1",
                payload={"track_id": "track-1"},
            )
            store.finish_job(job_key="matching:video-1:track-1", status="failed", error_message="boom")
            retry_start = store.try_start_job(
                job_type="track_embedding_match",
                job_key="matching:video-1:track-1",
                job_id="msg-2",
                payload={"track_id": "track-1"},
            )
            store.finish_job(job_key="matching:video-1:track-1", status="completed")
            duplicate_after_complete = store.try_start_job(
                job_type="track_embedding_match",
                job_key="matching:video-1:track-1",
                job_id="msg-3",
                payload={"track_id": "track-1"},
            )

            self.assertTrue(first_start)
            self.assertTrue(retry_start)
            self.assertFalse(duplicate_after_complete)

    def test_system_config_validation_and_rollback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            config_service = SystemConfigService(db_path)

            with self.assertRaises(ValueError):
                config_service.update_config(
                    {"min_similarity": 0.4},
                    updated_by="admin@example.com",
                    admin_id="admin-1",
                )

            updated = config_service.update_config(
                {"min_similarity": 0.82, "min_margin": 0.09},
                updated_by="admin@example.com",
                admin_id="admin-1",
            )
            self.assertEqual(updated["min_similarity"], 0.82)
            self.assertEqual(updated["min_margin"], 0.09)

            history = config_service.list_change_history(limit=10)
            self.assertGreaterEqual(len(history), 2)
            self.assertEqual(history[0]["admin_id"], "admin-1")

            rollback = config_service.rollback_config(
                updated_by="admin@example.com",
                admin_id="admin-2",
                batch_id=history[0]["batch_id"],
            )
            self.assertEqual(rollback["config"]["min_similarity"], 0.75)
            self.assertEqual(rollback["config"]["min_margin"], 0.05)

    def test_aggregator_uses_top_quality_frames(self):
        aggregator = EmbeddingAggregator(
            max_similarity=0.999999,
            min_samples=3,
            min_quality_score=0.2,
            top_k=3,
        )
        faces_data = [
            {
                "embedding": [1.0, 0.0, 0.0],
                "quality_score": 0.95,
                "det_score": 0.95,
                "source_frame_index": 1,
                "eligible_for_aggregation": True,
            },
            {
                "embedding": [0.99, 0.1, 0.0],
                "quality_score": 0.88,
                "det_score": 0.9,
                "source_frame_index": 2,
                "eligible_for_aggregation": True,
            },
            {
                "embedding": [0.98, 0.02, 0.1],
                "quality_score": 0.83,
                "det_score": 0.88,
                "source_frame_index": 3,
                "eligible_for_aggregation": True,
            },
            {
                "embedding": [0.0, 1.0, 0.0],
                "quality_score": 0.05,
                "det_score": 0.7,
                "source_frame_index": 4,
                "eligible_for_aggregation": True,
            },
        ]

        result = aggregator.aggregate(faces_data)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["aggregation_method"], "mean_top_k_quality")
        self.assertEqual(result["used_frames_count"], 3)
        self.assertEqual(set(result["used_frame_indexes"]), {1, 2, 3})
        self.assertGreater(result["quality_avg"], 0.8)

    def test_aggregator_rejects_low_consistency_tracks(self):
        aggregator = EmbeddingAggregator(
            max_similarity=0.999999,
            min_samples=3,
            min_quality_score=0.2,
            top_k=3,
        )
        faces_data = [
            {
                "embedding": [1.0, 0.0, 0.0],
                "quality_score": 0.95,
                "det_score": 0.95,
                "source_frame_index": 1,
                "eligible_for_aggregation": True,
            },
            {
                "embedding": [0.0, 1.0, 0.0],
                "quality_score": 0.94,
                "det_score": 0.94,
                "source_frame_index": 2,
                "eligible_for_aggregation": True,
            },
            {
                "embedding": [0.0, 0.0, 1.0],
                "quality_score": 0.93,
                "det_score": 0.93,
                "source_frame_index": 3,
                "eligible_for_aggregation": True,
            },
        ]

        evaluation = aggregator.evaluate(faces_data, min_consistency=0.95)

        self.assertFalse(evaluation["accepted"])
        self.assertEqual(evaluation["rejection_reason"], "low_consistency")
        self.assertIsNone(evaluation["result"])

    def test_pipeline_store_lists_pool_track_embeddings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            store = PipelineStore(db_path)
            video = store.create_video(
                video_id="video-1",
                s3_path="s3://bucket/uploads/videos/video-1.mp4",
                pool_id="pool-1",
            )
            embedding = store.upsert_video_embedding(
                video_id=video["video_id"],
                track_id="track-1",
                camera_id=None,
                pool_id="pool-1",
                embedding=[1.0, 0.0, 0.0],
                frames_count=3,
                frames_received=6,
                embeddings_created=4,
                confidence=0.91,
                consistency=0.89,
                quality_avg=0.82,
                aggregation_method="mean_top_k_quality",
                keyframe_s3="s3://bucket/keyframes/track-1.jpg",
            )
            store.upsert_video_frame_embedding(
                video_id=video["video_id"],
                track_id="track-1",
                frame_index=7,
                frame_timestamp="2026-03-24T10:00:00+00:00",
                embedding=[1.0, 0.0, 0.0],
                pool_id="pool-1",
                quality_score=0.9,
                video_embedding_id=embedding["video_embedding_id"],
                used_for_track_embedding=True,
            )

            tracks, next_cursor = store.list_pool_track_embeddings("pool-1", limit=10)
            frames = store.list_video_frame_embeddings(video["video_id"])

            self.assertIsNone(next_cursor)
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0]["track_id"], "track-1")
            self.assertEqual(tracks[0]["frames_count"], 3)
            self.assertEqual(tracks[0]["source_video_s3"], video["s3_path"])
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0]["used_for_track_embedding"])
        self.assertEqual(frames[0]["video_embedding_id"], embedding["video_embedding_id"])

    def test_matcher_rejects_ambiguous_margin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            users_db = UsersDB(db_path)
            self._insert_user(users_db, "user-a", "a@example.com", "pool-1", [[1.0, 0.0, 0.0]])
            self._insert_user(users_db, "user-b", "b@example.com", "pool-1", [[0.99, 0.08, 0.0]])

            config = ConfigStub()
            config.users_db_path = db_path
            config.matches_db_path = db_path
            config.sqlite_db_path = db_path
            config.min_margin = 0.2
            matcher = Matcher(users_db=users_db, config=config)

            result = matcher.match(
                {
                    "track_id": "track-1",
                    "pool_id": "pool-1",
                    "track_embedding": [1.0, 0.0, 0.0],
                    "frames_count": 4,
                    "consistency": 0.95,
                }
            )

            self.assertIsNone(result)

    def test_matcher_rejects_low_similarity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            users_db = UsersDB(db_path)
            self._insert_user(users_db, "user-a", "a@example.com", "pool-1", [[1.0, 0.0, 0.0]])

            config = ConfigStub()
            config.users_db_path = db_path
            config.matches_db_path = db_path
            config.sqlite_db_path = db_path
            config.min_similarity = 0.95
            matcher = Matcher(users_db=users_db, config=config)

            result = matcher.match(
                {
                    "track_id": "track-1",
                    "pool_id": "pool-1",
                    "track_embedding": [0.8, 0.6, 0.0],
                    "frames_count": 4,
                    "consistency": 0.95,
                }
            )

            self.assertIsNone(result)

    def test_pool_rematch_matches_multiple_tracks_in_same_video(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            pipeline_store = PipelineStore(db_path)
            users_db = UsersDB(db_path)
            matches_db = MatchesDB(db_path)

            video = pipeline_store.create_video(
                video_id="video-1",
                s3_path="s3://bucket/uploads/videos/video-1.mp4",
                pool_id="pool-1",
            )
            pipeline_store.upsert_video_embedding(
                video_id=video["video_id"],
                track_id="track-1",
                pool_id="pool-1",
                embedding=[1.0, 0.0, 0.0],
                frames_count=4,
                frames_received=6,
                embeddings_created=4,
                confidence=0.96,
                consistency=0.94,
                quality_avg=0.9,
                aggregation_method="mean_top_k_quality",
                keyframe_s3="s3://bucket/keyframes/track-1.jpg",
            )
            pipeline_store.upsert_video_embedding(
                video_id=video["video_id"],
                track_id="track-2",
                pool_id="pool-1",
                embedding=[0.0, 1.0, 0.0],
                frames_count=5,
                frames_received=7,
                embeddings_created=5,
                confidence=0.95,
                consistency=0.93,
                quality_avg=0.88,
                aggregation_method="mean_top_k_quality",
                keyframe_s3="s3://bucket/keyframes/track-2.jpg",
            )

            self._insert_user(users_db, "user-a", "a@example.com", "pool-1", [[1.0, 0.0, 0.0]])
            self._insert_user(users_db, "user-b", "b@example.com", "pool-1", [[0.0, 1.0, 0.0]])

            config = ConfigStub()
            config.users_db_path = db_path
            config.matches_db_path = db_path
            config.sqlite_db_path = db_path
            matcher = Matcher(users_db=users_db, config=config)
            consumer = MatchingConsumer(
                config=config,
                matcher=matcher,
                matches_db=matches_db,
                pipeline_store=pipeline_store,
                metrics=MetricsRegistry(),
            )
            consumer.sqs_client = FakeSQSClient()

            consumer._process_pool_rematch_job(
                {
                    "job_type": "rematch_pool_tracks",
                    "pool_id": "pool-1",
                    "batch_size": 10,
                }
            )

            with matches_db.store.connection() as conn:
                rows = conn.execute(
                    "SELECT user_id, track_id FROM matches ORDER BY track_id ASC"
                ).fetchall()

            self.assertEqual(
                [(row["user_id"], row["track_id"]) for row in rows],
                [("user-a", "track-1"), ("user-b", "track-2")],
            )

    def test_backfill_matches_existing_tracks_for_uploaded_user(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            pipeline_store = PipelineStore(db_path)
            users_db = UsersDB(db_path)
            matches_db = MatchesDB(db_path)

            video = pipeline_store.create_video(
                video_id="video-1",
                s3_path="s3://bucket/uploads/videos/video-1.mp4",
                pool_id="pool-1",
            )
            pipeline_store.upsert_video_embedding(
                video_id=video["video_id"],
                track_id="track-1",
                pool_id="pool-1",
                embedding=[1.0, 0.0, 0.0],
                frames_count=4,
                frames_received=8,
                embeddings_created=4,
                confidence=0.94,
                consistency=0.92,
                quality_avg=0.87,
                aggregation_method="mean_top_k_quality",
                keyframe_s3="s3://bucket/keyframes/track-1.jpg",
            )

            self._insert_user(users_db, "user-a", "a@example.com", "pool-1", [[1.0, 0.0, 0.0]])
            self._insert_user(users_db, "user-b", "b@example.com", "pool-1", [[0.0, 1.0, 0.0]])

            config = ConfigStub()
            config.users_db_path = db_path
            config.matches_db_path = db_path
            config.sqlite_db_path = db_path
            matcher = Matcher(users_db=users_db, config=config)
            consumer = MatchingConsumer(
                config=config,
                matcher=matcher,
                matches_db=matches_db,
                pipeline_store=pipeline_store,
                metrics=MetricsRegistry(),
            )
            consumer.sqs_client = FakeSQSClient()

            payload = {
                "job_type": "backfill_user_matches",
                "pool_id": "pool-1",
                "user_id": "user-a",
                "user_embedding_id": "upload-1",
                "user_embedding": [1.0, 0.0, 0.0],
                "batch_size": 10,
            }

            consumer._process_backfill_user_job(payload)
            consumer._process_backfill_user_job(payload)

            with matches_db.store.connection() as conn:
                rows = conn.execute(
                    "SELECT user_id, track_id FROM matches ORDER BY id ASC"
                ).fetchall()

            self.assertEqual([(row["user_id"], row["track_id"]) for row in rows], [("user-a", "track-1")])
            metrics = pipeline_store.get_metrics(prefix="matching.")
            self.assertEqual(metrics.get("matching.total_tracks_processed"), 1)
            self.assertEqual(metrics.get("matching.matches_created"), 1)

    def test_matching_consumer_dead_letters_invalid_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            pipeline_store = PipelineStore(db_path)
            users_db = UsersDB(db_path)
            matches_db = MatchesDB(db_path)

            config = ConfigStub()
            config.users_db_path = db_path
            config.matches_db_path = db_path
            config.sqlite_db_path = db_path
            matcher = Matcher(users_db=users_db, config=config)
            consumer = MatchingConsumer(
                config=config,
                matcher=matcher,
                matches_db=matches_db,
                pipeline_store=pipeline_store,
                metrics=MetricsRegistry(),
            )
            consumer.sqs_client = FakeSQSClient()

            consumer._handle_message(
                {
                    "MessageId": "msg-1",
                    "ReceiptHandle": "receipt-1",
                    "Body": "{not-json",
                    "Attributes": {"ApproximateReceiveCount": "1"},
                }
            )

            self.assertEqual(len(consumer.sqs_client.sent_messages), 1)
            dlq_message = json.loads(consumer.sqs_client.sent_messages[0]["MessageBody"])
            self.assertEqual(dlq_message["reason"], "permanent_error")
            self.assertEqual(dlq_message["worker_type"], "matching-service")
            self.assertEqual(dlq_message["receive_count"], 1)
            self.assertEqual(
                consumer.sqs_client.deleted_receipts,
                [{"QueueUrl": config.input_sqs_url, "ReceiptHandle": "receipt-1"}],
            )
            metrics = pipeline_store.get_metrics(prefix="worker.matching-service.")
            self.assertEqual(metrics.get("worker.matching-service.dead_lettered"), 1)

    def test_pipeline_store_prunes_frame_embeddings_and_retention(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            store = PipelineStore(db_path)
            video = store.create_video(
                video_id="video-1",
                s3_path="s3://bucket/uploads/videos/video-1.mp4",
                pool_id="pool-1",
            )

            for frame_index, quality in enumerate([0.99, 0.95, 0.9, 0.85, 0.8, 0.05], start=1):
                store.upsert_video_frame_embedding(
                    video_id=video["video_id"],
                    track_id="track-1",
                    frame_index=frame_index,
                    frame_timestamp=f"2026-03-24T10:00:0{frame_index}+00:00",
                    embedding=[1.0, 0.0, 0.0],
                    pool_id="pool-1",
                    quality_score=quality,
                    used_for_track_embedding=frame_index <= 2,
                )
                store.upsert_video_debug_frame(
                    video_id=video["video_id"],
                    track_id="track-1",
                    frame_index=frame_index,
                    frame_timestamp=f"2026-03-24T10:00:0{frame_index}+00:00",
                    image_s3=f"s3://bucket/debug/track-1-{frame_index}.jpg",
                    embedding=[1.0, 0.0, 0.0],
                    quality_score=quality,
                    det_score=0.9,
                    face_size=120.0,
                    blur_score=220.0,
                    rejection_reason=None if quality >= 0.2 else "low_quality_score",
                    has_face=True,
                    is_valid=quality >= 0.2,
                    used_for_embedding=frame_index <= 2,
                )

            prune_summary = store.prune_track_frame_embeddings(
                video_id=video["video_id"],
                track_id="track-1",
                keep_top_n=5,
                min_quality_score=0.2,
            )
            frames = store.list_video_frame_embeddings(video["video_id"])

            self.assertEqual(prune_summary["deleted_low_quality"], 1)
            self.assertEqual(len(frames), 5)

            with store.store.connection() as conn:
                conn.execute(
                    "UPDATE video_frame_embeddings SET created_at = '2020-01-01T00:00:00+00:00'"
                )
                conn.execute(
                    "UPDATE video_debug_frames SET created_at = '2020-01-01T00:00:00+00:00'"
                )

            cleanup_summary = store.cleanup_expired_artifacts(retention_days=7, debug_retention_days=7)
            self.assertEqual(cleanup_summary["deleted_frame_embeddings"], 5)
            self.assertEqual(cleanup_summary["deleted_debug_frames"], 6)

    def test_debug_compare_response_contains_real_frame_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            store = PipelineStore(db_path)
            video = store.create_video(
                video_id="video-1",
                s3_path="s3://bucket/uploads/videos/video-1.mp4",
                pool_id="pool-1",
            )
            track_embedding = store.upsert_video_embedding(
                video_id=video["video_id"],
                track_id="track-1",
                pool_id="pool-1",
                embedding=[1.0, 0.0, 0.0],
                frames_count=3,
                frames_received=4,
                embeddings_created=3,
                confidence=0.93,
                consistency=0.91,
                quality_avg=0.88,
                aggregation_method="mean_top_k_quality",
                keyframe_s3="s3://bucket/keyframes/track-1.jpg",
            )
            store.upsert_video_frame_embedding(
                video_id=video["video_id"],
                track_id="track-1",
                frame_index=1,
                frame_timestamp="2026-03-24T10:00:00+00:00",
                embedding=[1.0, 0.0, 0.0],
                pool_id="pool-1",
                quality_score=0.92,
                video_embedding_id=track_embedding["video_embedding_id"],
                used_for_track_embedding=True,
            )
            store.upsert_video_debug_frame(
                video_id=video["video_id"],
                track_id="track-1",
                frame_index=1,
                video_embedding_id=track_embedding["video_embedding_id"],
                frame_timestamp="2026-03-24T10:00:00+00:00",
                image_s3="s3://bucket/debug/frame-1.jpg",
                embedding=[1.0, 0.0, 0.0],
                quality_score=0.92,
                det_score=0.96,
                face_size=140.0,
                blur_score=260.0,
                rejection_reason=None,
                has_face=True,
                is_valid=True,
                used_for_embedding=True,
            )
            store.upsert_video_debug_frame(
                video_id=video["video_id"],
                track_id="track-1",
                frame_index=2,
                frame_timestamp="2026-03-24T10:00:01+00:00",
                image_s3="s3://bucket/debug/frame-2.jpg",
                quality_score=0.1,
                det_score=0.4,
                face_size=30.0,
                blur_score=20.0,
                rejection_reason="small_face",
                has_face=True,
                is_valid=False,
                used_for_embedding=False,
            )

            response = build_debug_compare_response(
                video_id=video["video_id"],
                video=video,
                pool={"pool_id": "pool-1", "name": "Pool 1"},
                pool_users=[{"user_id": "user-a", "email": "a@example.com"}],
                pool_reference_images=[
                    {
                        "user_embedding_id": "ref-1",
                        "user_id": "user-a",
                        "email": "a@example.com",
                        "embedding": [1.0, 0.0, 0.0],
                        "source_image_url": "https://example.com/ref-1.jpg",
                        "created_at": "2026-03-24T00:00:00+00:00",
                    }
                ],
                video_embeddings=[
                    {
                        **track_embedding,
                        "keyframe_url": "https://example.com/keyframe.jpg",
                    }
                ],
                frame_embeddings=store.list_video_frame_embeddings(video["video_id"]),
                debug_frames=[
                    {
                        **item,
                        "image_url": f"https://example.com/frame-{item['frame_index']}.jpg",
                    }
                    for item in store.list_video_debug_frames(video["video_id"])
                ],
                matches=[],
                similarity_threshold=0.75,
                margin_threshold=0.05,
            )

            self.assertEqual(response["track_summaries"][0]["track_id"], "track-1")
            self.assertEqual(response["track_summaries"][0]["used_frame_indexes"], [1])
            self.assertEqual(response["track_summaries"][0]["frames_received"], 4)
            self.assertIsNone(response["track_summaries"][0]["match_rejection_reason"])
            self.assertEqual(response["track_summaries"][0]["final_verdict"], "match")
            self.assertEqual(response["track_summaries"][0]["decision_explanation"], "Match accepted because similarity and margin both passed")
            self.assertEqual(len(response["debug_frames"]), 2)
            self.assertAlmostEqual(response["debug_frames"][0]["similarity"], 1.0, places=4)
            self.assertEqual(response["debug_frames"][0]["det_score"], 0.96)
            self.assertEqual(response["debug_frames"][1]["rejection_reason"], "small_face")

    def test_match_persistence_stores_explainability_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            pipeline_store = PipelineStore(db_path)
            users_db = UsersDB(db_path)
            matches_db = MatchesDB(db_path)

            video = pipeline_store.create_video(
                video_id="video-1",
                s3_path="s3://bucket/uploads/videos/video-1.mp4",
                pool_id="pool-1",
            )
            pipeline_store.upsert_video_embedding(
                video_id=video["video_id"],
                track_id="track-1",
                pool_id="pool-1",
                embedding=[1.0, 0.0, 0.0],
                frames_count=4,
                frames_received=6,
                embeddings_created=4,
                confidence=0.96,
                consistency=0.94,
                quality_avg=0.9,
                aggregation_method="mean_top_k_quality",
                keyframe_s3="s3://bucket/keyframes/track-1.jpg",
            )
            self._insert_user(users_db, "user-a", "a@example.com", "pool-1", [[1.0, 0.0, 0.0]])
            self._insert_user(users_db, "user-b", "b@example.com", "pool-1", [[0.2, 0.98, 0.0]])

            config = ConfigStub()
            config.users_db_path = db_path
            config.matches_db_path = db_path
            config.sqlite_db_path = db_path
            matcher = Matcher(users_db=users_db, config=config)
            consumer = MatchingConsumer(
                config=config,
                matcher=matcher,
                matches_db=matches_db,
                pipeline_store=pipeline_store,
                metrics=MetricsRegistry(),
            )
            consumer.sqs_client = FakeSQSClient()

            outcome = consumer._match_and_persist(
                {
                    "job_type": "track_embedding_match",
                    "track_id": "track-1",
                    "pool_id": "pool-1",
                    "video_id": "video-1",
                    "track_embedding": [1.0, 0.0, 0.0],
                    "frames_count": 4,
                    "consistency": 0.95,
                }
            )

            self.assertEqual(outcome, "matched")
            with matches_db.store.connection() as conn:
                row = conn.execute(
                    """
                    SELECT best_similarity, second_best_similarity, margin, threshold_used,
                           margin_threshold_used, decision_explanation
                    FROM matches
                    WHERE track_id = 'track-1'
                    """
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertAlmostEqual(float(row["best_similarity"]), 1.0, places=4)
            self.assertIsNotNone(row["second_best_similarity"])
            self.assertIsNotNone(row["margin"])
            self.assertAlmostEqual(float(row["threshold_used"]), 0.75, places=4)
            self.assertAlmostEqual(float(row["margin_threshold_used"]), 0.05, places=4)
            self.assertEqual(row["decision_explanation"], "Match accepted because similarity and margin both passed")

    def test_match_storage_keeps_existing_track_when_new_score_is_not_significantly_better(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            matches_db = MatchesDB(db_path)

            first = matches_db.add_match(
                {
                    "user_id": "user-a",
                    "track_id": "track-1",
                    "score": 0.85,
                    "confidence": 0.85,
                    "distance": 0.15,
                    "embeddings_used": 1,
                    "distance_mean": 0.15,
                    "distance_std": 0.0,
                    "distance_max": 0.15,
                    "best_similarity": 0.85,
                    "second_best_similarity": 0.75,
                    "margin": 0.10,
                    "threshold_used": 0.75,
                    "margin_threshold_used": 0.05,
                    "decision_explanation": "first",
                }
            )
            second = matches_db.add_match(
                {
                    "user_id": "user-b",
                    "track_id": "track-1",
                    "score": 0.87,
                    "confidence": 0.87,
                    "distance": 0.13,
                    "embeddings_used": 1,
                    "distance_mean": 0.13,
                    "distance_std": 0.0,
                    "distance_max": 0.13,
                    "best_similarity": 0.87,
                    "second_best_similarity": 0.80,
                    "margin": 0.07,
                    "threshold_used": 0.75,
                    "margin_threshold_used": 0.05,
                    "decision_explanation": "second",
                },
                significant_improvement_margin=0.05,
            )

            self.assertEqual(first.status, "inserted")
            self.assertEqual(second.status, "retained_existing")
            with matches_db.store.connection() as conn:
                row = conn.execute(
                    "SELECT user_id, score FROM matches WHERE track_id = 'track-1'"
                ).fetchone()
            self.assertEqual(row["user_id"], "user-a")
            self.assertAlmostEqual(float(row["score"]), 0.85, places=4)

    def test_match_storage_reassigns_track_when_new_score_is_significantly_better(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            matches_db = MatchesDB(db_path)

            matches_db.add_match(
                {
                    "user_id": "user-a",
                    "track_id": "track-1",
                    "score": 0.84,
                    "confidence": 0.84,
                    "distance": 0.16,
                    "embeddings_used": 1,
                    "distance_mean": 0.16,
                    "distance_std": 0.0,
                    "distance_max": 0.16,
                    "best_similarity": 0.84,
                    "second_best_similarity": 0.74,
                    "margin": 0.10,
                    "threshold_used": 0.75,
                    "margin_threshold_used": 0.05,
                    "decision_explanation": "first",
                }
            )
            replacement = matches_db.add_match(
                {
                    "user_id": "user-b",
                    "track_id": "track-1",
                    "score": 0.93,
                    "confidence": 0.93,
                    "distance": 0.07,
                    "embeddings_used": 1,
                    "distance_mean": 0.07,
                    "distance_std": 0.0,
                    "distance_max": 0.07,
                    "best_similarity": 0.93,
                    "second_best_similarity": 0.78,
                    "margin": 0.15,
                    "threshold_used": 0.75,
                    "margin_threshold_used": 0.05,
                    "decision_explanation": "replacement",
                },
                significant_improvement_margin=0.05,
            )

            self.assertEqual(replacement.status, "reassigned")
            with matches_db.store.connection() as conn:
                row = conn.execute(
                    "SELECT user_id, score, decision_explanation FROM matches WHERE track_id = 'track-1'"
                ).fetchone()
            self.assertEqual(row["user_id"], "user-b")
            self.assertAlmostEqual(float(row["score"]), 0.93, places=4)
            self.assertEqual(row["decision_explanation"], "replacement")

    def test_debug_compare_response_reports_track_validation_reason(self):
        response = build_debug_compare_response(
            video_id="video-1",
            video={"video_id": "video-1", "pool_id": "pool-1"},
            pool={"pool_id": "pool-1", "name": "Pool 1"},
            pool_users=[{"user_id": "user-a", "email": "a@example.com"}],
            pool_reference_images=[
                {
                    "user_embedding_id": "ref-1",
                    "user_id": "user-a",
                    "email": "a@example.com",
                    "embedding": [1.0, 0.0, 0.0],
                    "source_image_url": "https://example.com/ref-1.jpg",
                    "created_at": "2026-03-24T00:00:00+00:00",
                }
            ],
            video_embeddings=[
                {
                    "video_embedding_id": "ve-1",
                    "track_id": "track-1",
                    "embedding": [1.0, 0.0, 0.0],
                    "frames_count": 1,
                    "frames_received": 1,
                    "embeddings_created": 1,
                    "consistency": 0.95,
                    "quality_avg": 0.9,
                    "aggregation_method": "mean_top_k_quality",
                    "keyframe_url": "https://example.com/keyframe.jpg",
                }
            ],
            frame_embeddings=[],
            debug_frames=[],
            matches=[],
            similarity_threshold=0.75,
            margin_threshold=0.05,
            min_track_embeddings=3,
            min_track_consistency=0.75,
        )

        self.assertEqual(response["track_summaries"][0]["decision_reason"], "min_frames_per_track")
        self.assertEqual(response["track_summaries"][0]["final_verdict"], "no_match")

    def _insert_user(self, users_db: UsersDB, user_id: str, email: str, pool_id: str, embeddings):
        with users_db.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, email, password_hash, password_salt, role, pool_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, email, None, None, "user", pool_id, "2026-03-24T00:00:00+00:00"),
            )
            for embedding in embeddings:
                conn.execute(
                    """
                    INSERT INTO user_embeddings (user_id, embedding_json, source_image_s3, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, json.dumps(list(embedding)), None, "2026-03-24T00:00:00+00:00"),
                )


if __name__ == "__main__":
    unittest.main()
