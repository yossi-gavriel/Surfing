import os
import uuid
from datetime import datetime, timezone
from typing import Any

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cameras (
                    camera_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
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

    def create_video(
        self,
        *,
        s3_path: str,
        status: str = "uploaded",
        source_type: str = "video",
        camera_id: str | None = None,
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
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        }
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO videos (
                    video_id, s3_path, status, source_type, camera_id, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["video_id"],
                    record["s3_path"],
                    record["status"],
                    record["source_type"],
                    record["camera_id"],
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
                SELECT video_id, s3_path, status, source_type, camera_id, error_message, created_at, updated_at
                FROM videos
                WHERE video_id = ?
                """,
                (video_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_videos(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT video_id, s3_path, status, source_type, camera_id, error_message, created_at, updated_at
                FROM videos
                ORDER BY datetime(created_at) DESC, video_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

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

    def upsert_camera(
        self,
        *,
        name: str,
        url: str,
        active: bool = True,
        camera_id: str | None = None,
    ) -> dict[str, Any]:
        existing = self.get_camera(camera_id) if camera_id else None
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "camera_id": camera_id or str(uuid.uuid4()),
            "name": name.strip(),
            "url": url.strip(),
            "active": bool(active),
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
        }

        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO cameras (camera_id, name, url, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera_id) DO UPDATE SET
                    name = excluded.name,
                    url = excluded.url,
                    active = excluded.active,
                    updated_at = excluded.updated_at
                """,
                (
                    record["camera_id"],
                    record["name"],
                    record["url"],
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
                SELECT camera_id, name, url, active, created_at, updated_at
                FROM cameras
                WHERE camera_id = ?
                """,
                (camera_id,),
            ).fetchone()
        return self._camera_from_row(row) if row else None

    def list_cameras(self, active_only: bool = False) -> list[dict[str, Any]]:
        query = """
            SELECT camera_id, name, url, active, created_at, updated_at
            FROM cameras
        """
        params: tuple[Any, ...] = ()
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY datetime(updated_at) DESC, camera_id DESC"
        with self.store.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._camera_from_row(row) for row in rows]

    def list_active_cameras(self) -> list[dict[str, Any]]:
        return self.list_cameras(active_only=True)

    def _camera_from_row(self, row) -> dict[str, Any]:
        return {
            "camera_id": row["camera_id"],
            "name": row["name"],
            "url": row["url"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _normalize_status(self, status: str) -> str:
        normalized = (status or "").strip().lower()
        if normalized not in self.VALID_VIDEO_STATUSES:
            raise ValueError(f"Unsupported video status: {status}")
        return normalized
