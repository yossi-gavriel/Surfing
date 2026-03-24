import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_SERVICE_ROOT = PROJECT_ROOT / "services" / "api-gateway"

for path in (API_SERVICE_ROOT, PROJECT_ROOT):
    resolved = str(path)
    if resolved not in sys.path:
        sys.path.insert(0, resolved)

if "insightface" not in sys.modules:
    insightface_module = types.ModuleType("insightface")
    insightface_app_module = types.ModuleType("insightface.app")

    class _StubFaceAnalysis:
        def __init__(self, *args, **kwargs):
            pass

        def prepare(self, *args, **kwargs):
            return None

        def get(self, image):
            return []

    insightface_app_module.FaceAnalysis = _StubFaceAnalysis
    insightface_module.app = insightface_app_module
    sys.modules["insightface"] = insightface_module
    sys.modules["insightface.app"] = insightface_app_module

from src.main import create_app
from src.security import create_access_token, get_jwt_config


class FakeFaceService:
    def __init__(self):
        self.calls = []

    def extract_embedding(self, image_bytes: bytes, *, allow_multiple_faces: bool = False):
        self.calls.append(
            {
                "size_bytes": len(image_bytes),
                "allow_multiple_faces": allow_multiple_faces,
            }
        )
        return {
            "embedding": [1.0, 0.0, 0.0],
            "face_size": 120.0,
            "blur_score": 150.0,
            "det_score": 0.99,
            "faces_detected": 1,
        }


class FakeMediaService:
    def __init__(self):
        self.default_bucket = "test-bucket"
        self.uploads = []

    def upload_bytes(self, *, data: bytes, key: str, content_type: str | None = None) -> str:
        self.uploads.append(
            {
                "key": key,
                "size_bytes": len(data),
                "content_type": content_type,
            }
        )
        return f"s3://{self.default_bucket}/{key}"

    def get_presigned_url(self, s3_path: str | None = None, *, key: str | None = None, expires_in: int = 3600):
        target = key or s3_path
        if not target:
            return None
        if s3_path and s3_path.startswith(("http://", "https://")):
            return s3_path
        if s3_path and s3_path.startswith("s3://"):
            _, _, remainder = s3_path.partition("s3://")
            bucket, _, object_key = remainder.partition("/")
            return f"https://example.invalid/{bucket}/{object_key}?expires_in={expires_in}"
        return f"https://example.invalid/{self.default_bucket}/{str(target).lstrip('/')}?expires_in={expires_in}"


class FakeSQSClient:
    def __init__(self):
        self.sent_messages = []

    def send_message(self, QueueUrl, MessageBody):
        message = {
            "QueueUrl": QueueUrl,
            "MessageBody": json.loads(MessageBody),
        }
        self.sent_messages.append(message)
        return {"MessageId": f"msg-{len(self.sent_messages)}"}


class ApiProductionReadinessTests(unittest.TestCase):
    def test_schema_bootstrap_handles_missing_columns_before_indexes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE videos (
                    video_id TEXT PRIMARY KEY,
                    s3_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT,
                    password_salt TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    score REAL NOT NULL,
                    confidence REAL NOT NULL,
                    distance REAL NOT NULL,
                    embeddings_used INTEGER NOT NULL,
                    distance_mean REAL NOT NULL,
                    distance_std REAL NOT NULL,
                    distance_max REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
            conn.close()

            from shared.utils.pipeline_store import PipelineStore
            from src.db import SQLiteDB

            pipeline_store = PipelineStore(db_path)
            users_db = SQLiteDB(data_dir=temp_dir, db_path=db_path)

            with pipeline_store.store.connection() as verify_conn:
                video_columns = pipeline_store.store.table_columns(verify_conn, "videos")
                match_columns = users_db.store.table_columns(verify_conn, "matches")
                indexes = {
                    row["name"]
                    for row in verify_conn.execute("PRAGMA index_list(videos)").fetchall()
                }

            self.assertIn("pool_id", video_columns)
            self.assertIn("error_message", video_columns)
            self.assertIn("pool_id", match_columns)
            self.assertIn("idx_videos_pool_created", indexes)

    def test_gateway_starts_without_jwt_secret_and_reports_health(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["SQLITE_DB_PATH"] = os.path.join(temp_dir, "surf_ai.db")
            os.environ.pop("JWT_SECRET", None)
            get_jwt_config.cache_clear()

            app = create_app()
            client = TestClient(app)

            response = client.get("/health")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ready"])
            self.assertIn("version", payload)

    def test_real_user_flow_validation_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "surf_ai.db")
            os.environ["SQLITE_DB_PATH"] = db_path
            os.environ["JWT_SECRET"] = "1234567890abcdef1234567890abcdef"
            os.environ["SQS_QUEUE_URL"] = "https://example.invalid/ingestion"
            os.environ["MATCHING_INPUT_SQS_URL"] = "https://example.invalid/matching"
            get_jwt_config.cache_clear()

            app = create_app()
            app.state.face_service = FakeFaceService()
            app.state.get_face_service = lambda: app.state.face_service
            app.state.media_service = FakeMediaService()
            app.state.admin_sqs_client = FakeSQSClient()

            user, admin, pool = self._seed_users(app)
            user_token = create_access_token(user)
            admin_token = create_access_token(admin)
            client = TestClient(app)

            face_started = time.perf_counter()
            face_response = client.post(
                "/me/reference-images",
                headers={"Authorization": f"Bearer {user_token}"},
                files={"files": ("face.jpg", io.BytesIO(b"face-image"), "image/jpeg")},
            )
            face_elapsed_ms = round((time.perf_counter() - face_started) * 1000, 2)

            video_started = time.perf_counter()
            video_response = client.post(
                "/admin/upload-video",
                headers={"Authorization": f"Bearer {admin_token}"},
                files={"file": ("wave.mp4", io.BytesIO(b"video-data"), "video/mp4")},
            )
            video_elapsed_ms = round((time.perf_counter() - video_started) * 1000, 2)

            self.assertEqual(face_response.status_code, 200)
            self.assertEqual(video_response.status_code, 200)

            video_id = video_response.json()["video_id"]
            self._persist_match(
                app,
                user_id=user["user_id"],
                pool_id=pool["pool_id"],
                video_id=video_id,
            )

            verify_started = time.perf_counter()
            verify_response = client.get(
                "/user/videos",
                headers={"Authorization": f"Bearer {user_token}"},
            )
            verify_elapsed_ms = round((time.perf_counter() - verify_started) * 1000, 2)

            self.assertEqual(verify_response.status_code, 200)
            verify_payload = verify_response.json()
            self.assertEqual(len(verify_payload), 1)
            self.assertEqual(verify_payload[0]["video_id"], video_id)

            report = {
                "timings_ms": {
                    "upload_face": face_elapsed_ms,
                    "upload_video": video_elapsed_ms,
                    "verify_match": verify_elapsed_ms,
                },
                "api_responses": {
                    "upload_face": face_response.json(),
                    "upload_video": video_response.json(),
                    "verify_match": verify_payload,
                },
                "logs": {
                    "face_service_calls": app.state.face_service.calls,
                    "media_uploads": app.state.media_service.uploads,
                    "sqs_messages": app.state.admin_sqs_client.sent_messages,
                },
            }
            print(json.dumps(report, indent=2, sort_keys=True))

    def _seed_users(self, app):
        admin = app.state.db.create_user(
            email="admin@example.com",
            password_hash="hash",
            password_salt="salt",
        )
        user = app.state.db.create_user(
            email="user@example.com",
            password_hash="hash",
            password_salt="salt",
        )
        assert admin is not None
        assert user is not None

        with app.state.db.store.connection() as conn:
            conn.execute("UPDATE users SET role = 'admin' WHERE user_id = ?", (admin["user_id"],))

        pool = app.state.db.create_pool(name="Production Pool", created_by=admin["user_id"])
        admin = app.state.db.update_user_pool(user_id=admin["user_id"], pool_id=pool["pool_id"])
        user = app.state.db.update_user_pool(user_id=user["user_id"], pool_id=pool["pool_id"])
        assert admin is not None
        assert user is not None
        return user, admin, pool

    def _persist_match(self, app, *, user_id: str, pool_id: str, video_id: str):
        now = "2026-03-24T12:00:00+00:00"
        app.state.pipeline_store.update_video_status(video_id, "completed")
        app.state.pipeline_store.update_video_diagnostics(
            video_id,
            {
                "frame_processor": {
                    "started_at": now,
                    "completed_at": now,
                    "processing_seconds": 0.1,
                    "output_tracks": 1,
                },
                "embedding_service": {
                    "started_at": now,
                    "completed_at": now,
                    "processing_seconds": 0.1,
                    "tracks_received": 1,
                },
                "matching_service": {
                    "started_at": now,
                    "completed_at": now,
                    "processing_seconds": 0.1,
                    "tracks": {
                        "track-1": {
                            "decision": "match",
                            "best_similarity": 0.99,
                            "margin": 0.12,
                        }
                    },
                },
            },
        )
        with app.state.db.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO matches (
                    user_id, track_id, camera_id, video_id, source_video_s3,
                    timestamp, keyframe, keyframe_s3, score, confidence, distance,
                    embeddings_used, distance_mean, distance_std, distance_max,
                    second_best_score, score_margin, best_similarity, second_best_similarity,
                    margin, threshold_used, margin_threshold_used, decision_reason,
                    decision_explanation, pool_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    "track-1",
                    None,
                    video_id,
                    f"s3://test-bucket/uploads/videos/{video_id}.mp4",
                    now,
                    None,
                    f"s3://test-bucket/keyframes/{video_id}.jpg",
                    0.99,
                    0.98,
                    0.01,
                    1,
                    0.01,
                    0.0,
                    0.01,
                    0.87,
                    0.12,
                    0.99,
                    0.87,
                    0.12,
                    0.75,
                    0.05,
                    "match",
                    "Match accepted because similarity and margin both passed",
                    pool_id,
                    now,
                ),
            )


if __name__ == "__main__":
    unittest.main()
