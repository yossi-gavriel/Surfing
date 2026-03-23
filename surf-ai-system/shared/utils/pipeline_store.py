import json
import os
import uuid
from datetime import datetime, timezone
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
            self._ensure_column(conn, "videos", "pool_id", "TEXT")
            self._ensure_column(conn, "videos", "assigned_user_id", "TEXT")
            self._ensure_column(conn, "videos", "diagnostics_json", "TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS video_embeddings (
                    id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    camera_id TEXT,
                    embedding_json TEXT NOT NULL,
                    frames_received INTEGER NOT NULL DEFAULT 0,
                    embeddings_created INTEGER NOT NULL DEFAULT 0,
                    confidence REAL,
                    consistency REAL,
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
            self._ensure_column(conn, "video_embeddings", "keyframe_s3", "TEXT")
            self._ensure_column(conn, "video_embeddings", "start_time", "TEXT")
            self._ensure_column(conn, "video_embeddings", "end_time", "TEXT")
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
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_video_debug_frames_video_track
                ON video_debug_frames(video_id, track_id, frame_index ASC)
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
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cameras_active
                ON cameras(active, updated_at DESC)
                """
            )
            self._ensure_column(conn, "cameras", "pool_id", "TEXT")

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
        existing = self.get_video(video_id)
        if not existing:
            return None
        diagnostics = existing.get("diagnostics") or {}
        merged = self._deep_merge_dicts(diagnostics, patch)
        return self.set_video_diagnostics(video_id, merged)

    def upsert_video_embedding(
        self,
        *,
        video_id: str,
        track_id: str,
        embedding: list[float],
        camera_id: str | None = None,
        frames_received: int = 0,
        embeddings_created: int = 0,
        confidence: float | None = None,
        consistency: float | None = None,
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
                    SET camera_id = ?, embedding_json = ?, frames_received = ?, embeddings_created = ?,
                        confidence = ?, consistency = ?, keyframe_s3 = ?, start_time = ?, end_time = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        camera_id,
                        json.dumps(normalized.astype(float).tolist()),
                        int(frames_received),
                        int(embeddings_created),
                        confidence,
                        consistency,
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
                        id, video_id, track_id, camera_id, embedding_json, frames_received,
                        embeddings_created, confidence, consistency, keyframe_s3, start_time, end_time,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        embedding_id,
                        video_id,
                        track_id,
                        camera_id,
                        json.dumps(normalized.astype(float).tolist()),
                        int(frames_received),
                        int(embeddings_created),
                        confidence,
                        consistency,
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
            "embedding": normalized.astype(float).tolist(),
            "frames_received": int(frames_received),
            "embeddings_created": int(embeddings_created),
            "confidence": confidence,
            "consistency": consistency,
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
                    id,
                    video_id,
                    track_id,
                    camera_id,
                    embedding_json,
                    frames_received,
                    embeddings_created,
                    confidence,
                    consistency,
                    keyframe_s3,
                    start_time,
                    end_time,
                    created_at,
                    updated_at
                FROM video_embeddings
                WHERE video_id = ?
                ORDER BY datetime(created_at) ASC, id ASC
                """,
                (video_id,),
            ).fetchall()
        return [self._video_embedding_from_row(row) for row in rows]

    def upsert_video_debug_frame(
        self,
        *,
        video_id: str,
        track_id: str,
        frame_index: int,
        frame_timestamp: str | None = None,
        image_s3: str | None = None,
        bbox: list[float] | None = None,
        face_bbox: list[float] | None = None,
        embedding: list[float] | None = None,
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
                    SET frame_timestamp = ?, image_s3 = ?, bbox_json = ?, face_bbox_json = ?, embedding_json = ?,
                        has_face = ?, is_valid = ?, used_for_embedding = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        frame_timestamp,
                        image_s3,
                        bbox_json,
                        face_bbox_json,
                        embedding_json,
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
                        id, video_id, track_id, frame_index, frame_timestamp, image_s3, bbox_json, face_bbox_json,
                        embedding_json, has_face, is_valid, used_for_embedding, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        frame_id,
                        video_id,
                        track_id,
                        int(frame_index),
                        frame_timestamp,
                        image_s3,
                        bbox_json,
                        face_bbox_json,
                        embedding_json,
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
            "frame_timestamp": frame_timestamp,
            "image_s3": image_s3,
            "bbox": None if bbox is None else [float(value) for value in bbox],
            "face_bbox": None if face_bbox is None else [float(value) for value in face_bbox],
            "embedding": None if normalized_embedding is None else normalized_embedding.astype(float).tolist(),
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
                    frame_timestamp,
                    image_s3,
                    bbox_json,
                    face_bbox_json,
                    embedding_json,
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

    def _video_embedding_from_row(self, row) -> dict[str, Any]:
        raw_embedding = json.loads(row["embedding_json"])
        normalized = normalize_embedding_vector(raw_embedding)
        embedding = raw_embedding if normalized is None else normalized.astype(float).tolist()
        return {
            "video_embedding_id": row["id"],
            "video_id": row["video_id"],
            "track_id": row["track_id"],
            "camera_id": row["camera_id"],
            "embedding": embedding,
            "frames_received": int(row["frames_received"]),
            "embeddings_created": int(row["embeddings_created"]),
            "confidence": row["confidence"],
            "consistency": row["consistency"],
            "keyframe_s3": row["keyframe_s3"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
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
            "frame_timestamp": row["frame_timestamp"],
            "image_s3": row["image_s3"],
            "bbox": bbox,
            "face_bbox": face_bbox,
            "embedding": None if normalized_embedding is None else normalized_embedding.astype(float).tolist(),
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
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {row["name"] for row in rows}
        if column_name not in existing_columns:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
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
