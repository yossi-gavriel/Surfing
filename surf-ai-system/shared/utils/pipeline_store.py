import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.utils.embeddings import normalize_embedding_vector
from shared.utils.sqlite_store import SQLiteStore


class PipelineStore:
    VALID_VIDEO_STATUSES = {"uploaded", "processing", "completed", "failed"}

    def __init__(self, db_path: str):
        self.db_path = db_path
        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.store = SQLiteStore(db_path)
        self._create_schema()

    def _create_schema(self) -> None:
        with self.store.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    video_id TEXT PRIMARY KEY,
                    s3_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_type TEXT NOT NULL DEFAULT 'video',
                    camera_id TEXT,
                    pool_id TEXT,
                    assigned_user_id TEXT,
                    diagnostics_json TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_videos_created_at
                ON videos(datetime(created_at) DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_videos_status
                ON videos(status)
                """
            )
            self._ensure_column(conn, "videos", "source_type", "TEXT NOT NULL DEFAULT 'video'")
            self._ensure_column(conn, "videos", "camera_id", "TEXT")
            self._ensure_column(conn, "videos", "pool_id", "TEXT")
            self._ensure_column(conn, "videos", "assigned_user_id", "TEXT")
            self._ensure_column(conn, "videos", "diagnostics_json", "TEXT")
            self._ensure_column(conn, "videos", "error_message", "TEXT")
            self._create_index_if_columns(
                conn,
                table_name="videos",
                required_columns=["pool_id", "created_at", "video_id"],
                create_sql="""
                CREATE INDEX IF NOT EXISTS idx_videos_pool_created
                ON videos(pool_id, datetime(created_at) DESC, video_id DESC)
                """,
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS video_embeddings (
                    id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    camera_id TEXT,
                    pool_id TEXT,
                    embedding_json TEXT NOT NULL,
                    frames_count INTEGER NOT NULL DEFAULT 0,
                    frames_received INTEGER NOT NULL DEFAULT 0,
                    embeddings_created INTEGER NOT NULL DEFAULT 0,
                    confidence REAL,
                    consistency REAL,
                    quality_avg REAL,
                    aggregation_method TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(video_id, track_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_video_embeddings_video_id
                ON video_embeddings(video_id, created_at DESC)
                """
            )
            self._ensure_column(conn, "video_embeddings", "camera_id", "TEXT")
            self._ensure_column(conn, "video_embeddings", "pool_id", "TEXT")
            self._ensure_column(conn, "video_embeddings", "frames_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "video_embeddings", "frames_received", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "video_embeddings", "embeddings_created", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "video_embeddings", "confidence", "REAL")
            self._ensure_column(conn, "video_embeddings", "consistency", "REAL")
            self._ensure_column(conn, "video_embeddings", "keyframe_s3", "TEXT")
            self._ensure_column(conn, "video_embeddings", "start_time", "TEXT")
            self._ensure_column(conn, "video_embeddings", "end_time", "TEXT")
            self._ensure_column(conn, "video_embeddings", "quality_avg", "REAL")
            self._ensure_column(conn, "video_embeddings", "aggregation_method", "TEXT")
            self._create_index_if_columns(
                conn,
                table_name="video_embeddings",
                required_columns=["pool_id", "track_id", "created_at"],
                create_sql="""
                CREATE INDEX IF NOT EXISTS idx_video_embeddings_pool_track
                ON video_embeddings(pool_id, track_id, created_at DESC)
                """,
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS video_frame_embeddings (
                    id TEXT PRIMARY KEY,
                    video_embedding_id TEXT,
                    video_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    pool_id TEXT,
                    frame_index INTEGER NOT NULL,
                    frame_timestamp TEXT,
                    embedding_json TEXT NOT NULL,
                    quality_score REAL,
                    used_for_track_embedding INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(video_id, track_id, frame_index)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_video_frame_embeddings_video_track
                ON video_frame_embeddings(video_id, track_id, frame_index ASC)
                """
            )
            self._ensure_column(conn, "video_frame_embeddings", "video_embedding_id", "TEXT")
            self._ensure_column(conn, "video_frame_embeddings", "pool_id", "TEXT")
            self._ensure_column(conn, "video_frame_embeddings", "frame_timestamp", "TEXT")
            self._ensure_column(conn, "video_frame_embeddings", "quality_score", "REAL")
            self._ensure_column(conn, "video_frame_embeddings", "used_for_track_embedding", "INTEGER NOT NULL DEFAULT 0")
            self._create_index_if_columns(
                conn,
                table_name="video_frame_embeddings",
                required_columns=["pool_id", "created_at"],
                create_sql="""
                CREATE INDEX IF NOT EXISTS idx_video_frame_embeddings_pool
                ON video_frame_embeddings(pool_id, created_at DESC)
                """,
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_video_frame_embeddings_track_quality
                ON video_frame_embeddings(video_id, track_id, used_for_track_embedding DESC, quality_score DESC, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS video_debug_frames (
                    id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    frame_index INTEGER NOT NULL,
                    frame_timestamp TEXT,
                    image_s3 TEXT,
                    bbox_json TEXT,
                    face_bbox_json TEXT,
                    embedding_json TEXT,
                    has_face INTEGER NOT NULL DEFAULT 0,
                    is_valid INTEGER NOT NULL DEFAULT 0,
                    used_for_embedding INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(video_id, track_id, frame_index)
                )
                """
            )
            self._ensure_column(conn, "video_debug_frames", "frame_timestamp", "TEXT")
            self._ensure_column(conn, "video_debug_frames", "image_s3", "TEXT")
            self._ensure_column(conn, "video_debug_frames", "bbox_json", "TEXT")
            self._ensure_column(conn, "video_debug_frames", "face_bbox_json", "TEXT")
            self._ensure_column(conn, "video_debug_frames", "embedding_json", "TEXT")
            self._ensure_column(conn, "video_debug_frames", "has_face", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "video_debug_frames", "is_valid", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "video_debug_frames", "used_for_embedding", "INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_video_debug_frames_video_track
                ON video_debug_frames(video_id, track_id, frame_index ASC)
                """
            )
            self._ensure_column(conn, "video_debug_frames", "video_embedding_id", "TEXT")
            self._ensure_column(conn, "video_debug_frames", "quality_score", "REAL")
            self._ensure_column(conn, "video_debug_frames", "det_score", "REAL")
            self._ensure_column(conn, "video_debug_frames", "face_size", "REAL")
            self._ensure_column(conn, "video_debug_frames", "blur_score", "REAL")
            self._ensure_column(conn, "video_debug_frames", "rejection_reason", "TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_video_debug_frames_created
                ON video_debug_frames(datetime(created_at) DESC, video_id, track_id, frame_index)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cameras (
                    camera_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    pool_id TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "cameras", "pool_id", "TEXT")
            self._ensure_column(conn, "cameras", "active", "INTEGER NOT NULL DEFAULT 1")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cameras_active
                ON cameras(active, updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_metrics (
                    metric_name TEXT PRIMARY KEY,
                    metric_value INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_system_metrics_updated_at
                ON system_metrics(datetime(updated_at) DESC, metric_name)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_execution_locks (
                    job_key TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    job_id TEXT,
                    status TEXT NOT NULL,
                    payload_json TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    last_error TEXT
                )
                """
            )
            self._ensure_column(conn, "job_execution_locks", "job_id", "TEXT")
            self._ensure_column(conn, "job_execution_locks", "payload_json", "TEXT")
            self._ensure_column(conn, "job_execution_locks", "completed_at", "TEXT")
            self._ensure_column(conn, "job_execution_locks", "last_error", "TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_job_execution_locks_status
                ON job_execution_locks(status, datetime(updated_at) DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_leases (
                    worker_type TEXT PRIMARY KEY,
                    leader_id TEXT NOT NULL,
                    metadata_json TEXT,
                    acquired_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "worker_leases", "metadata_json", "TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_worker_leases_expires_at
                ON worker_leases(datetime(expires_at) ASC, worker_type)
                """
            )

    def create_video(
        self,
        *,
        s3_path: str,
        status: str = "uploaded",
        source_type: str = "video",
        camera_id: str | None = None,
        pool_id: str | None = None,
        video_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_status = self._normalize_status(status)
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "video_id": video_id or str(uuid.uuid4()),
            "s3_path": s3_path,
            "status": normalized_status,
            "source_type": source_type,
            "camera_id": camera_id,
            "pool_id": pool_id,
            "assigned_user_id": None,
            "diagnostics": {},
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        }
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO videos (
                    video_id, s3_path, status, source_type, camera_id, pool_id, assigned_user_id, diagnostics_json, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["video_id"],
                    record["s3_path"],
                    record["status"],
                    record["source_type"],
                    record["camera_id"],
                    record["pool_id"],
                    record["assigned_user_id"],
                    json.dumps(record["diagnostics"]),
                    record["error_message"],
                    record["created_at"],
                    record["updated_at"],
                ),
            )
        return record

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT video_id, s3_path, status, source_type, camera_id, pool_id, assigned_user_id, diagnostics_json, error_message, created_at, updated_at
                FROM videos
                WHERE video_id = ?
                """,
                (video_id,),
            ).fetchone()
        return self._video_from_row(row) if row else None

    def list_videos(self, limit: int = 100, pool_id: str | None = None) -> list[dict[str, Any]]:
        query = """
                SELECT video_id, s3_path, status, source_type, camera_id, pool_id, assigned_user_id, diagnostics_json, error_message, created_at, updated_at
                FROM videos
        """
        params: tuple[Any, ...]
        if pool_id is None:
            params = (limit,)
        else:
            query += " WHERE pool_id = ?"
            params = (pool_id, limit)
        query += """
                ORDER BY datetime(created_at) DESC, video_id DESC
                LIMIT ?
        """
        with self.store.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._video_from_row(row) for row in rows]

    def update_video_status(
        self,
        video_id: str,
        status: str,
        *,
        error_message: str | None = None,
    ) -> dict[str, Any] | None:
        normalized_status = self._normalize_status(status)
        updated_at = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as conn:
            conn.execute(
                """
                    UPDATE videos
                    SET status = ?, error_message = ?, updated_at = ?
                    WHERE video_id = ?
                """,
                (normalized_status, error_message, updated_at, video_id),
            )
        return self.get_video(video_id)

    def assign_video_user(
        self,
        *,
        video_id: str,
        user_id: str | None,
    ) -> dict[str, Any] | None:
        updated_at = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as conn:
            conn.execute(
                """
                UPDATE videos
                SET assigned_user_id = ?, updated_at = ?
                WHERE video_id = ?
                """,
                (user_id, updated_at, video_id),
            )
        return self.get_video(video_id)

    def set_video_diagnostics(
        self,
        video_id: str,
        diagnostics: dict[str, Any],
    ) -> dict[str, Any] | None:
        updated_at = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as conn:
            conn.execute(
                """
                UPDATE videos
                SET diagnostics_json = ?, updated_at = ?
                WHERE video_id = ?
                """,
                (json.dumps(diagnostics), updated_at, video_id),
            )
        return self.get_video(video_id)

    def update_video_diagnostics(
        self,
        video_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any] | None:
        updated_at = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT diagnostics_json
                    FROM videos
                    WHERE video_id = ?
                    """,
                    (video_id,),
                ).fetchone()
                if not row:
                    conn.execute("ROLLBACK")
                    return None

                diagnostics = json.loads(row["diagnostics_json"]) if row["diagnostics_json"] else {}
                merged = self._deep_merge_dicts(diagnostics, patch)
                conn.execute(
                    """
                    UPDATE videos
                    SET diagnostics_json = ?, updated_at = ?
                    WHERE video_id = ?
                    """,
                    (json.dumps(merged), updated_at, video_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.get_video(video_id)

    def upsert_video_embedding(
        self,
        *,
        video_id: str,
        track_id: str,
        embedding: list[float],
        camera_id: str | None = None,
        pool_id: str | None = None,
        frames_count: int = 0,
        frames_received: int = 0,
        embeddings_created: int = 0,
        confidence: float | None = None,
        consistency: float | None = None,
        quality_avg: float | None = None,
        aggregation_method: str | None = None,
        keyframe_s3: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_embedding_vector(embedding)
        if normalized is None:
            raise ValueError("Video embedding cannot be normalized")

        now = datetime.now(timezone.utc).isoformat()
        created = False
        with self.store.connection() as conn:
            existing = conn.execute(
                """
                SELECT id, created_at
                FROM video_embeddings
                WHERE video_id = ? AND track_id = ?
                """,
                (video_id, track_id),
            ).fetchone()

            if existing:
                embedding_id = existing["id"]
                created_at = existing["created_at"]
                conn.execute(
                    """
                    UPDATE video_embeddings
                    SET camera_id = ?, pool_id = ?, embedding_json = ?, frames_count = ?, frames_received = ?,
                        embeddings_created = ?, confidence = ?, consistency = ?, quality_avg = ?,
                        aggregation_method = ?, keyframe_s3 = ?, start_time = ?, end_time = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        camera_id,
                        pool_id,
                        json.dumps(normalized.astype(float).tolist()),
                        int(frames_count),
                        int(frames_received),
                        int(embeddings_created),
                        confidence,
                        consistency,
                        quality_avg,
                        aggregation_method,
                        keyframe_s3,
                        start_time,
                        end_time,
                        now,
                        embedding_id,
                    ),
                )
            else:
                created = True
                embedding_id = str(uuid.uuid4())
                created_at = now
                conn.execute(
                    """
                    INSERT INTO video_embeddings (
                        id, video_id, track_id, camera_id, pool_id, embedding_json, frames_count, frames_received,
                        embeddings_created, confidence, consistency, quality_avg, aggregation_method,
                        keyframe_s3, start_time, end_time,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        embedding_id,
                        video_id,
                        track_id,
                        camera_id,
                        pool_id,
                        json.dumps(normalized.astype(float).tolist()),
                        int(frames_count),
                        int(frames_received),
                        int(embeddings_created),
                        confidence,
                        consistency,
                        quality_avg,
                        aggregation_method,
                        keyframe_s3,
                        start_time,
                        end_time,
                        created_at,
                        now,
                    ),
                )

        return {
            "video_embedding_id": embedding_id,
            "video_id": video_id,
            "track_id": track_id,
            "camera_id": camera_id,
            "pool_id": pool_id,
            "embedding": normalized.astype(float).tolist(),
            "frames_count": int(frames_count),
            "frames_received": int(frames_received),
            "embeddings_created": int(embeddings_created),
            "confidence": confidence,
            "consistency": consistency,
            "quality_avg": quality_avg,
            "aggregation_method": aggregation_method,
            "keyframe_s3": keyframe_s3,
            "start_time": start_time,
            "end_time": end_time,
            "created_at": created_at,
            "updated_at": now,
            "created": created,
        }

    def list_video_embeddings(self, video_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    ve.id,
                    ve.video_id,
                    ve.track_id,
                    ve.camera_id,
                    ve.pool_id,
                    ve.embedding_json,
                    ve.frames_count,
                    ve.frames_received,
                    ve.embeddings_created,
                    ve.confidence,
                    ve.consistency,
                    ve.quality_avg,
                    ve.aggregation_method,
                    ve.keyframe_s3,
                    ve.start_time,
                    ve.end_time,
                    ve.created_at,
                    ve.updated_at,
                    v.s3_path AS source_video_s3
                FROM video_embeddings ve
                INNER JOIN videos v ON v.video_id = ve.video_id
                WHERE ve.video_id = ?
                ORDER BY datetime(ve.created_at) ASC, ve.id ASC
                """,
                (video_id,),
            ).fetchall()
        return [self._video_embedding_from_row(row) for row in rows]

    def list_pool_track_embeddings(
        self,
        pool_id: str,
        *,
        limit: int = 100,
        cursor_created_at: str | None = None,
        cursor_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
        query = """
                SELECT
                    ve.id,
                    ve.video_id,
                    ve.track_id,
                    ve.camera_id,
                    ve.pool_id,
                    ve.embedding_json,
                    ve.frames_count,
                    ve.frames_received,
                    ve.embeddings_created,
                    ve.confidence,
                    ve.consistency,
                    ve.quality_avg,
                    ve.aggregation_method,
                    ve.keyframe_s3,
                    ve.start_time,
                    ve.end_time,
                    ve.created_at,
                    ve.updated_at,
                    v.s3_path AS source_video_s3
                FROM video_embeddings ve
                INNER JOIN videos v ON v.video_id = ve.video_id
                WHERE ve.pool_id = ?
        """
        params: list[Any] = [pool_id]
        if cursor_created_at and cursor_id:
            query += """
                AND (
                    ve.created_at > ?
                    OR (ve.created_at = ? AND ve.id > ?)
                )
            """
            params.extend([cursor_created_at, cursor_created_at, cursor_id])
        query += """
                ORDER BY ve.created_at ASC, ve.id ASC
                LIMIT ?
        """
        params.append(int(limit) + 1)
        with self.store.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()

        has_more = len(rows) > limit
        selected_rows = rows[:limit]
        items = [self._video_embedding_from_row(row) for row in selected_rows]
        if not has_more or not selected_rows:
            return items, None

        last_row = selected_rows[-1]
        return items, {
            "cursor_created_at": last_row["created_at"],
            "cursor_id": last_row["id"],
        }

    def upsert_video_frame_embedding(
        self,
        *,
        video_id: str,
        track_id: str,
        frame_index: int,
        embedding: list[float],
        pool_id: str | None = None,
        frame_timestamp: str | None = None,
        quality_score: float | None = None,
        video_embedding_id: str | None = None,
        used_for_track_embedding: bool = False,
    ) -> dict[str, Any]:
        normalized_embedding = normalize_embedding_vector(embedding)
        if normalized_embedding is None:
            raise ValueError("Frame embedding cannot be normalized")

        now = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as conn:
            existing = conn.execute(
                """
                SELECT id, created_at
                FROM video_frame_embeddings
                WHERE video_id = ? AND track_id = ? AND frame_index = ?
                """,
                (video_id, track_id, int(frame_index)),
            ).fetchone()

            embedding_json = json.dumps(normalized_embedding.astype(float).tolist())
            if existing:
                frame_embedding_id = existing["id"]
                created_at = existing["created_at"]
                conn.execute(
                    """
                    UPDATE video_frame_embeddings
                    SET video_embedding_id = ?, pool_id = ?, frame_timestamp = ?, embedding_json = ?,
                        quality_score = ?, used_for_track_embedding = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        video_embedding_id,
                        pool_id,
                        frame_timestamp,
                        embedding_json,
                        quality_score,
                        1 if used_for_track_embedding else 0,
                        now,
                        frame_embedding_id,
                    ),
                )
            else:
                frame_embedding_id = str(uuid.uuid4())
                created_at = now
                conn.execute(
                    """
                    INSERT INTO video_frame_embeddings (
                        id, video_embedding_id, video_id, track_id, pool_id, frame_index, frame_timestamp,
                        embedding_json, quality_score, used_for_track_embedding, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        frame_embedding_id,
                        video_embedding_id,
                        video_id,
                        track_id,
                        pool_id,
                        int(frame_index),
                        frame_timestamp,
                        embedding_json,
                        quality_score,
                        1 if used_for_track_embedding else 0,
                        created_at,
                        now,
                    ),
                )

        return {
            "frame_embedding_id": frame_embedding_id,
            "video_embedding_id": video_embedding_id,
            "video_id": video_id,
            "track_id": track_id,
            "pool_id": pool_id,
            "frame_index": int(frame_index),
            "frame_timestamp": frame_timestamp,
            "embedding": normalized_embedding.astype(float).tolist(),
            "quality_score": quality_score,
            "used_for_track_embedding": bool(used_for_track_embedding),
            "created_at": created_at,
            "updated_at": now,
        }

    def list_video_frame_embeddings(self, video_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    video_embedding_id,
                    video_id,
                    track_id,
                    pool_id,
                    frame_index,
                    frame_timestamp,
                    embedding_json,
                    quality_score,
                    used_for_track_embedding,
                    created_at,
                    updated_at
                FROM video_frame_embeddings
                WHERE video_id = ?
                ORDER BY track_id ASC, frame_index ASC, id ASC
                """,
                (video_id,),
            ).fetchall()
        return [self._video_frame_embedding_from_row(row) for row in rows]

    def upsert_video_debug_frame(
        self,
        *,
        video_id: str,
        track_id: str,
        frame_index: int,
        video_embedding_id: str | None = None,
        frame_timestamp: str | None = None,
        image_s3: str | None = None,
        bbox: list[float] | None = None,
        face_bbox: list[float] | None = None,
        embedding: list[float] | None = None,
        quality_score: float | None = None,
        det_score: float | None = None,
        face_size: float | None = None,
        blur_score: float | None = None,
        rejection_reason: str | None = None,
        has_face: bool = False,
        is_valid: bool = False,
        used_for_embedding: bool = False,
    ) -> dict[str, Any]:
        normalized_embedding = None
        if embedding is not None:
            normalized_embedding = normalize_embedding_vector(embedding)

        now = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as conn:
            existing = conn.execute(
                """
                SELECT id, created_at
                FROM video_debug_frames
                WHERE video_id = ? AND track_id = ? AND frame_index = ?
                """,
                (video_id, track_id, int(frame_index)),
            ).fetchone()

            embedding_json = None if normalized_embedding is None else json.dumps(normalized_embedding.astype(float).tolist())
            bbox_json = None if bbox is None else json.dumps([float(value) for value in bbox])
            face_bbox_json = None if face_bbox is None else json.dumps([float(value) for value in face_bbox])

            if existing:
                frame_id = existing["id"]
                created_at = existing["created_at"]
                conn.execute(
                    """
                    UPDATE video_debug_frames
                    SET video_embedding_id = ?, frame_timestamp = ?, image_s3 = ?, bbox_json = ?, face_bbox_json = ?,
                        embedding_json = ?, quality_score = ?, det_score = ?, face_size = ?, blur_score = ?,
                        rejection_reason = ?, has_face = ?, is_valid = ?, used_for_embedding = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        video_embedding_id,
                        frame_timestamp,
                        image_s3,
                        bbox_json,
                        face_bbox_json,
                        embedding_json,
                        quality_score,
                        det_score,
                        face_size,
                        blur_score,
                        rejection_reason,
                        1 if has_face else 0,
                        1 if is_valid else 0,
                        1 if used_for_embedding else 0,
                        now,
                        frame_id,
                    ),
                )
            else:
                frame_id = str(uuid.uuid4())
                created_at = now
                conn.execute(
                    """
                    INSERT INTO video_debug_frames (
                        id, video_id, track_id, frame_index, video_embedding_id, frame_timestamp, image_s3, bbox_json, face_bbox_json,
                        embedding_json, quality_score, det_score, face_size, blur_score, rejection_reason,
                        has_face, is_valid, used_for_embedding, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        frame_id,
                        video_id,
                        track_id,
                        int(frame_index),
                        video_embedding_id,
                        frame_timestamp,
                        image_s3,
                        bbox_json,
                        face_bbox_json,
                        embedding_json,
                        quality_score,
                        det_score,
                        face_size,
                        blur_score,
                        rejection_reason,
                        1 if has_face else 0,
                        1 if is_valid else 0,
                        1 if used_for_embedding else 0,
                        created_at,
                        now,
                    ),
                )

        return {
            "debug_frame_id": frame_id,
            "video_id": video_id,
            "track_id": track_id,
            "frame_index": int(frame_index),
            "video_embedding_id": video_embedding_id,
            "frame_timestamp": frame_timestamp,
            "image_s3": image_s3,
            "bbox": None if bbox is None else [float(value) for value in bbox],
            "face_bbox": None if face_bbox is None else [float(value) for value in face_bbox],
            "embedding": None if normalized_embedding is None else normalized_embedding.astype(float).tolist(),
            "quality_score": quality_score,
            "det_score": det_score,
            "face_size": face_size,
            "blur_score": blur_score,
            "rejection_reason": rejection_reason,
            "has_face": bool(has_face),
            "is_valid": bool(is_valid),
            "used_for_embedding": bool(used_for_embedding),
            "created_at": created_at,
            "updated_at": now,
        }

    def list_video_debug_frames(self, video_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    video_id,
                    track_id,
                    frame_index,
                    video_embedding_id,
                    frame_timestamp,
                    image_s3,
                    bbox_json,
                    face_bbox_json,
                    embedding_json,
                    quality_score,
                    det_score,
                    face_size,
                    blur_score,
                    rejection_reason,
                    has_face,
                    is_valid,
                    used_for_embedding,
                    created_at,
                    updated_at
                FROM video_debug_frames
                WHERE video_id = ?
                ORDER BY track_id ASC, frame_index ASC, id ASC
                """,
                (video_id,),
            ).fetchall()
        return [self._video_debug_frame_from_row(row) for row in rows]

    def prune_track_frame_embeddings(
        self,
        *,
        video_id: str,
        track_id: str,
        keep_top_n: int,
        min_quality_score: float | None = None,
    ) -> dict[str, int]:
        keep_limit = max(int(keep_top_n), 0)
        pruned_low_quality = 0
        pruned_overflow = 0
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                candidate_rows = conn.execute(
                    """
                    SELECT id, quality_score
                    FROM video_frame_embeddings
                    WHERE video_id = ? AND track_id = ?
                    ORDER BY used_for_track_embedding DESC, COALESCE(quality_score, 0.0) DESC, frame_index ASC, id ASC
                    """,
                    (video_id, track_id),
                ).fetchall()
                if not candidate_rows:
                    conn.commit()
                    return {"deleted_low_quality": 0, "deleted_overflow": 0}

                keep_ids: set[str] = set()
                kept_count = 0
                for row in candidate_rows:
                    quality_score = row["quality_score"]
                    if (
                        min_quality_score is not None
                        and quality_score is not None
                        and float(quality_score) < float(min_quality_score)
                    ):
                        continue
                    if kept_count < keep_limit:
                        keep_ids.add(row["id"])
                        kept_count += 1

                delete_ids: list[str] = []
                for row in candidate_rows:
                    quality_score = row["quality_score"]
                    if row["id"] in keep_ids:
                        continue
                    if (
                        min_quality_score is not None
                        and quality_score is not None
                        and float(quality_score) < float(min_quality_score)
                    ):
                        pruned_low_quality += 1
                    else:
                        pruned_overflow += 1
                    delete_ids.append(row["id"])

                if delete_ids:
                    conn.executemany(
                        "DELETE FROM video_frame_embeddings WHERE id = ?",
                        [(frame_id,) for frame_id in delete_ids],
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "deleted_low_quality": pruned_low_quality,
            "deleted_overflow": pruned_overflow,
        }

    def cleanup_expired_artifacts(
        self,
        *,
        retention_days: int,
        debug_retention_days: int | None = None,
    ) -> dict[str, Any]:
        retention_days = max(int(retention_days), 0)
        debug_retention_days = retention_days if debug_retention_days is None else max(int(debug_retention_days), 0)
        deleted_frame_embeddings = 0
        deleted_debug_frames = 0
        expired_debug_images: list[str] = []
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                expired_debug_rows = conn.execute(
                    """
                    SELECT DISTINCT image_s3
                    FROM video_debug_frames
                    WHERE image_s3 IS NOT NULL AND datetime(created_at) < datetime('now', ?)
                    """,
                    (f"-{debug_retention_days} days",),
                ).fetchall()
                expired_debug_images = [row["image_s3"] for row in expired_debug_rows if row["image_s3"]]
                deleted_frame_embeddings = conn.execute(
                    """
                    DELETE FROM video_frame_embeddings
                    WHERE datetime(created_at) < datetime('now', ?)
                    """,
                    (f"-{retention_days} days",),
                ).rowcount
                deleted_debug_frames = conn.execute(
                    """
                    DELETE FROM video_debug_frames
                    WHERE datetime(created_at) < datetime('now', ?)
                    """,
                    (f"-{debug_retention_days} days",),
                ).rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "deleted_frame_embeddings": int(deleted_frame_embeddings or 0),
            "deleted_debug_frames": int(deleted_debug_frames or 0),
            "deleted_debug_image_s3_paths": expired_debug_images,
        }

    def upsert_camera(
        self,
        *,
        name: str,
        url: str,
        active: bool = True,
        camera_id: str | None = None,
        pool_id: str | None = None,
    ) -> dict[str, Any]:
        existing = self.get_camera(camera_id) if camera_id else None
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "camera_id": camera_id or str(uuid.uuid4()),
            "name": name.strip(),
            "url": url.strip(),
            "pool_id": pool_id,
            "active": bool(active),
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
        }

        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO cameras (camera_id, name, url, pool_id, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera_id) DO UPDATE SET
                    name = excluded.name,
                    url = excluded.url,
                    pool_id = excluded.pool_id,
                    active = excluded.active,
                    updated_at = excluded.updated_at
                """,
                (
                    record["camera_id"],
                    record["name"],
                    record["url"],
                    record["pool_id"],
                    1 if record["active"] else 0,
                    record["created_at"],
                    record["updated_at"],
                ),
            )
        return self.get_camera(record["camera_id"]) or record

    def get_camera(self, camera_id: str | None) -> dict[str, Any] | None:
        if not camera_id:
            return None
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT camera_id, name, url, pool_id, active, created_at, updated_at
                FROM cameras
                WHERE camera_id = ?
                """,
                (camera_id,),
            ).fetchone()
        return self._camera_from_row(row) if row else None

    def list_cameras(self, active_only: bool = False, pool_id: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT camera_id, name, url, pool_id, active, created_at, updated_at
            FROM cameras
        """
        params: list[Any] = []
        conditions: list[str] = []
        if active_only:
            conditions.append("active = 1")
        if pool_id is not None:
            conditions.append("pool_id = ?")
            params.append(pool_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY datetime(updated_at) DESC, camera_id DESC"
        with self.store.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._camera_from_row(row) for row in rows]

    def list_active_cameras(self, pool_id: str | None = None) -> list[dict[str, Any]]:
        return self.list_cameras(active_only=True, pool_id=pool_id)

    def increment_metric(self, metric_name: str, value: int = 1) -> dict[str, Any]:
        metric_name = (metric_name or "").strip()
        if not metric_name:
            raise ValueError("Metric name is required")
        increment_by = int(value)
        now = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO system_metrics (metric_name, metric_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(metric_name) DO UPDATE SET
                    metric_value = system_metrics.metric_value + excluded.metric_value,
                    updated_at = excluded.updated_at
                """,
                (metric_name, increment_by, now),
            )
            row = conn.execute(
                """
                SELECT metric_name, metric_value, updated_at
                FROM system_metrics
                WHERE metric_name = ?
                """,
                (metric_name,),
            ).fetchone()
        return {
            "metric_name": row["metric_name"],
            "metric_value": int(row["metric_value"]),
            "updated_at": row["updated_at"],
        }

    def get_metrics(self, *, prefix: str | None = None) -> dict[str, int]:
        query = """
            SELECT metric_name, metric_value
            FROM system_metrics
        """
        params: tuple[Any, ...] = ()
        if prefix:
            query += " WHERE metric_name LIKE ?"
            params = (f"{prefix}%",)
        query += " ORDER BY metric_name ASC"
        with self.store.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return {
            row["metric_name"]: int(row["metric_value"])
            for row in rows
        }

    def try_start_job(
        self,
        *,
        job_type: str,
        job_key: str,
        job_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        normalized_key = (job_key or "").strip()
        if not normalized_key:
            raise ValueError("Job key is required")

        now = datetime.now(timezone.utc).isoformat()
        payload_json = None if payload is None else json.dumps(payload, sort_keys=True)
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    """
                    SELECT status
                    FROM job_execution_locks
                    WHERE job_key = ?
                    """,
                    (normalized_key,),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO job_execution_locks (
                            job_key, job_type, job_id, status, payload_json, started_at, updated_at
                        ) VALUES (?, ?, ?, 'in_progress', ?, ?, ?)
                        """,
                        (normalized_key, job_type, job_id, payload_json, now, now),
                    )
                    conn.commit()
                    return True

                if existing["status"] == "completed":
                    conn.rollback()
                    return False
                if existing["status"] == "in_progress":
                    conn.rollback()
                    return False

                conn.execute(
                    """
                    UPDATE job_execution_locks
                    SET job_type = ?, job_id = ?, status = 'in_progress', payload_json = ?,
                        started_at = ?, updated_at = ?, completed_at = NULL, last_error = NULL
                    WHERE job_key = ?
                    """,
                    (job_type, job_id, payload_json, now, now, normalized_key),
                )
                conn.commit()
                return existing["status"] != "in_progress"
            except Exception:
                conn.rollback()
                raise

    def finish_job(
        self,
        *,
        job_key: str,
        status: str,
        error_message: str | None = None,
    ) -> None:
        normalized_status = (status or "").strip().lower()
        if normalized_status not in {"completed", "failed"}:
            raise ValueError("Unsupported job status")

        now = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as conn:
            conn.execute(
                """
                UPDATE job_execution_locks
                SET status = ?, updated_at = ?, completed_at = ?, last_error = ?
                WHERE job_key = ?
                """,
                (
                    normalized_status,
                    now,
                    now if normalized_status == "completed" else None,
                    error_message,
                    job_key,
                ),
            )

    def list_recent_jobs(
        self,
        *,
        limit: int = 25,
        job_type: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT
                job_key,
                job_type,
                job_id,
                status,
                payload_json,
                started_at,
                completed_at,
                updated_at,
                last_error
            FROM job_execution_locks
        """
        params: list[Any] = []
        if job_type:
            query += " WHERE job_type = ?"
            params.append(job_type)
        query += """
            ORDER BY datetime(updated_at) DESC, job_key DESC
            LIMIT ?
        """
        params.append(max(int(limit), 1))
        with self.store.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        jobs: list[dict[str, Any]] = []
        for row in rows:
            payload = None
            if row["payload_json"]:
                try:
                    payload = json.loads(row["payload_json"])
                except json.JSONDecodeError:
                    payload = row["payload_json"]
            jobs.append(
                {
                    "job_key": row["job_key"],
                    "job_type": row["job_type"],
                    "job_id": row["job_id"],
                    "status": row["status"],
                    "payload": payload,
                    "started_at": row["started_at"],
                    "completed_at": row["completed_at"],
                    "updated_at": row["updated_at"],
                    "last_error": row["last_error"],
                }
            )
        return jobs

    def try_acquire_worker_lease(
        self,
        *,
        worker_type: str,
        leader_id: str,
        ttl_seconds: int,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        normalized_type = (worker_type or "").strip()
        normalized_leader = (leader_id or "").strip()
        if not normalized_type or not normalized_leader:
            raise ValueError("worker_type and leader_id are required")

        ttl_seconds = max(int(ttl_seconds), 1)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        expires_iso = (now + timedelta(seconds=ttl_seconds)).isoformat()
        metadata_json = None if metadata is None else json.dumps(metadata, sort_keys=True)

        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT leader_id, acquired_at, expires_at
                    FROM worker_leases
                    WHERE worker_type = ?
                    """,
                    (normalized_type,),
                ).fetchone()

                if row is None:
                    conn.execute(
                        """
                        INSERT INTO worker_leases (
                            worker_type, leader_id, metadata_json, acquired_at, heartbeat_at, expires_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized_type,
                            normalized_leader,
                            metadata_json,
                            now_iso,
                            now_iso,
                            expires_iso,
                        ),
                    )
                    conn.commit()
                    return True

                expires_at = self._parse_datetime(row["expires_at"])
                lease_expired = expires_at is None or expires_at <= now
                same_leader = row["leader_id"] == normalized_leader
                if not lease_expired and not same_leader:
                    conn.rollback()
                    return False

                conn.execute(
                    """
                    UPDATE worker_leases
                    SET leader_id = ?, metadata_json = ?, acquired_at = ?, heartbeat_at = ?, expires_at = ?
                    WHERE worker_type = ?
                    """,
                    (
                        normalized_leader,
                        metadata_json,
                        row["acquired_at"] if same_leader else now_iso,
                        now_iso,
                        expires_iso,
                        normalized_type,
                    ),
                )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def release_worker_lease(
        self,
        *,
        worker_type: str,
        leader_id: str,
    ) -> None:
        with self.store.connection() as conn:
            conn.execute(
                """
                DELETE FROM worker_leases
                WHERE worker_type = ? AND leader_id = ?
                """,
                (worker_type, leader_id),
            )

    def get_worker_lease(self, worker_type: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT worker_type, leader_id, metadata_json, acquired_at, heartbeat_at, expires_at
                FROM worker_leases
                WHERE worker_type = ?
                """,
                (worker_type,),
            ).fetchone()
        if row is None:
            return None
        metadata = None
        if row["metadata_json"]:
            try:
                metadata = json.loads(row["metadata_json"])
            except json.JSONDecodeError:
                metadata = row["metadata_json"]
        return {
            "worker_type": row["worker_type"],
            "leader_id": row["leader_id"],
            "metadata": metadata,
            "acquired_at": row["acquired_at"],
            "heartbeat_at": row["heartbeat_at"],
            "expires_at": row["expires_at"],
        }

    def _camera_from_row(self, row) -> dict[str, Any]:
        return {
            "camera_id": row["camera_id"],
            "name": row["name"],
            "url": row["url"],
            "pool_id": row["pool_id"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _parse_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    def _video_embedding_from_row(self, row) -> dict[str, Any]:
        raw_embedding = json.loads(row["embedding_json"])
        normalized = normalize_embedding_vector(raw_embedding)
        embedding = raw_embedding if normalized is None else normalized.astype(float).tolist()
        return {
            "video_embedding_id": row["id"],
            "video_id": row["video_id"],
            "track_id": row["track_id"],
            "camera_id": row["camera_id"],
            "pool_id": row["pool_id"],
            "embedding": embedding,
            "frames_count": int(row["frames_count"]),
            "frames_received": int(row["frames_received"]),
            "embeddings_created": int(row["embeddings_created"]),
            "confidence": row["confidence"],
            "consistency": row["consistency"],
            "quality_avg": row["quality_avg"],
            "aggregation_method": row["aggregation_method"],
            "keyframe_s3": row["keyframe_s3"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "source_video_s3": row["source_video_s3"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _video_frame_embedding_from_row(self, row) -> dict[str, Any]:
        raw_embedding = json.loads(row["embedding_json"])
        normalized = normalize_embedding_vector(raw_embedding)
        embedding = raw_embedding if normalized is None else normalized.astype(float).tolist()
        return {
            "frame_embedding_id": row["id"],
            "video_embedding_id": row["video_embedding_id"],
            "video_id": row["video_id"],
            "track_id": row["track_id"],
            "pool_id": row["pool_id"],
            "frame_index": int(row["frame_index"]),
            "frame_timestamp": row["frame_timestamp"],
            "embedding": embedding,
            "quality_score": row["quality_score"],
            "used_for_track_embedding": bool(row["used_for_track_embedding"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _video_debug_frame_from_row(self, row) -> dict[str, Any]:
        raw_embedding = None if row["embedding_json"] is None else json.loads(row["embedding_json"])
        normalized_embedding = None if raw_embedding is None else normalize_embedding_vector(raw_embedding)
        bbox = None if row["bbox_json"] is None else json.loads(row["bbox_json"])
        face_bbox = None if row["face_bbox_json"] is None else json.loads(row["face_bbox_json"])
        return {
            "debug_frame_id": row["id"],
            "video_id": row["video_id"],
            "track_id": row["track_id"],
            "frame_index": int(row["frame_index"]),
            "video_embedding_id": row["video_embedding_id"],
            "frame_timestamp": row["frame_timestamp"],
            "image_s3": row["image_s3"],
            "bbox": bbox,
            "face_bbox": face_bbox,
            "embedding": None if normalized_embedding is None else normalized_embedding.astype(float).tolist(),
            "quality_score": row["quality_score"],
            "det_score": row["det_score"],
            "face_size": row["face_size"],
            "blur_score": row["blur_score"],
            "rejection_reason": row["rejection_reason"],
            "has_face": bool(row["has_face"]),
            "is_valid": bool(row["is_valid"]),
            "used_for_embedding": bool(row["used_for_embedding"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _video_from_row(self, row) -> dict[str, Any]:
        diagnostics: dict[str, Any] = {}
        raw_diagnostics = row["diagnostics_json"]
        if raw_diagnostics:
            try:
                diagnostics = json.loads(raw_diagnostics)
            except json.JSONDecodeError:
                diagnostics = {}

        return {
            "video_id": row["video_id"],
            "s3_path": row["s3_path"],
            "status": row["status"],
            "source_type": row["source_type"],
            "camera_id": row["camera_id"],
            "pool_id": row["pool_id"],
            "assigned_user_id": row["assigned_user_id"],
            "diagnostics": diagnostics,
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _ensure_column(self, conn, table_name: str, column_name: str, column_definition: str) -> None:
        self.store.ensure_column(conn, table_name, column_name, column_definition)

    def _create_index_if_columns(
        self,
        conn,
        *,
        table_name: str,
        required_columns: list[str],
        create_sql: str,
    ) -> None:
        self.store.create_index_if_columns(
            conn,
            table_name=table_name,
            required_columns=required_columns,
            create_sql=create_sql,
        )

    def _deep_merge_dicts(self, base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._deep_merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _normalize_status(self, status: str) -> str:
        normalized = (status or "").strip().lower()
        if normalized not in self.VALID_VIDEO_STATUSES:
            raise ValueError(f"Unsupported video status: {status}")
        return normalized
