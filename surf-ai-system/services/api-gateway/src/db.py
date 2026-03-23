import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from shared.utils.embeddings import normalize_embedding_vector
from shared.utils.sqlite_store import SQLiteStore, load_json_records


class SQLiteDB:
    def __init__(
        self,
        data_dir: str = "/app/data",
        db_path: str | None = None,
    ):
        self.data_dir = data_dir
        self.db_path = db_path or os.environ.get("SQLITE_DB_PATH") or os.path.join(data_dir, "surf_ai.db")
        self.legacy_users_file = os.path.join(data_dir, "users.json")
        self.legacy_matches_file = os.path.join(data_dir, "matches.json")
        self.store = SQLiteStore(self.db_path)
        self._create_schema()
        self._migrate_legacy_json()

    def _create_schema(self) -> None:
        with self.store.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
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
                CREATE TABLE IF NOT EXISTS user_embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_embeddings_user_id
                ON user_embeddings(user_id, id DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    camera_id TEXT,
                    video_id TEXT,
                    source_video_s3 TEXT,
                    timestamp TEXT,
                    keyframe TEXT,
                    keyframe_s3 TEXT,
                    score REAL NOT NULL,
                    confidence REAL NOT NULL,
                    distance REAL NOT NULL,
                    embeddings_used INTEGER NOT NULL,
                    distance_mean REAL NOT NULL,
                    distance_std REAL NOT NULL,
                    distance_max REAL NOT NULL,
                    second_best_score REAL,
                    score_margin REAL,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, track_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_matches_user_created
                ON matches(user_id, created_at DESC)
                """
            )

    def _migrate_legacy_json(self) -> None:
        if not self.store.table_has_rows("users"):
            users = load_json_records(self.legacy_users_file, "users")
            if users:
                with self.store.connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        for raw_user in users:
                            user_id = raw_user.get("user_id") or str(uuid.uuid4())
                            created_at = raw_user.get("created_at") or datetime.now(timezone.utc).isoformat()
                            conn.execute(
                                """
                                INSERT OR IGNORE INTO users (
                                    user_id, email, password_hash, password_salt, created_at
                                ) VALUES (?, ?, ?, ?, ?)
                                """,
                                (
                                    user_id,
                                    raw_user.get("email") or f"{user_id}@legacy.local",
                                    raw_user.get("password_hash"),
                                    raw_user.get("password_salt"),
                                    created_at,
                                ),
                            )

                            embeddings = raw_user.get("embeddings")
                            if embeddings is None and raw_user.get("embedding") is not None:
                                embeddings = [raw_user["embedding"]]
                            for embedding in embeddings or []:
                                conn.execute(
                                    """
                                    INSERT INTO user_embeddings (user_id, embedding_json, created_at)
                                    VALUES (?, ?, ?)
                                    """,
                                    (user_id, json.dumps(embedding), created_at),
                                )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise

        if not self.store.table_has_rows("matches"):
            matches = load_json_records(self.legacy_matches_file, "matches")
            if matches:
                with self.store.connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        for match in matches:
                            conn.execute(
                                """
                                INSERT OR IGNORE INTO matches (
                                    user_id, track_id, camera_id, video_id, source_video_s3,
                                    timestamp, keyframe, keyframe_s3, score, confidence, distance,
                                    embeddings_used, distance_mean, distance_std, distance_max,
                                    second_best_score, score_margin, created_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    match.get("user_id"),
                                    match.get("track_id"),
                                    match.get("camera_id"),
                                    match.get("video_id"),
                                    match.get("source_video_s3"),
                                    match.get("timestamp"),
                                    match.get("keyframe"),
                                    match.get("keyframe_s3"),
                                    float(match.get("score", 0.0)),
                                    float(match.get("confidence", 0.0)),
                                    float(match.get("distance", 0.0)),
                                    int(match.get("embeddings_used", 0)),
                                    float(match.get("distance_mean", 0.0)),
                                    float(match.get("distance_std", 0.0)),
                                    float(match.get("distance_max", 0.0)),
                                    match.get("second_best_score"),
                                    match.get("score_margin"),
                                    match.get("created_at") or datetime.now(timezone.utc).isoformat(),
                                ),
                            )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise

    def _user_from_row(self, row) -> dict[str, Any]:
        with self.store.connection() as conn:
            embeddings_rows = conn.execute(
                """
                SELECT embedding_json
                FROM user_embeddings
                WHERE user_id = ?
                ORDER BY id ASC
                """,
                (row["user_id"],),
            ).fetchall()

        return {
            "user_id": row["user_id"],
            "email": row["email"],
            "password_hash": row["password_hash"],
            "password_salt": row["password_salt"],
            "created_at": row["created_at"],
            "embeddings": [json.loads(item["embedding_json"]) for item in embeddings_rows],
        }

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT user_id, email, password_hash, password_salt, created_at
                FROM users
                WHERE email = ?
                """,
                (email,),
            ).fetchone()
        return self._user_from_row(row) if row else None

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT user_id, email, password_hash, password_salt, created_at
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        return self._user_from_row(row) if row else None

    def create_user(
        self,
        email: str,
        password_hash: str,
        password_salt: str,
    ) -> dict[str, Any] | None:
        user_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                exists = conn.execute(
                    "SELECT user_id FROM users WHERE email = ?",
                    (email,),
                ).fetchone()
                if exists:
                    conn.rollback()
                    return None

                conn.execute(
                    """
                    INSERT INTO users (user_id, email, password_hash, password_salt, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, email, password_hash, password_salt, created_at),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        return {
            "user_id": user_id,
            "email": email,
            "password_hash": password_hash,
            "password_salt": password_salt,
            "created_at": created_at,
            "embeddings": [],
        }

    def append_user_embedding(
        self,
        user_id: str,
        embedding: list[float],
        max_embeddings: int = 5,
    ) -> dict[str, Any] | None:
        created_at = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                user_row = conn.execute(
                    """
                    SELECT user_id, email, password_hash, password_salt, created_at
                    FROM users
                    WHERE user_id = ?
                    """,
                    (user_id,),
                ).fetchone()
                if not user_row:
                    conn.rollback()
                    return None

                conn.execute(
                    """
                    INSERT INTO user_embeddings (user_id, embedding_json, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, json.dumps(embedding), created_at),
                )

                overflow_rows = conn.execute(
                    """
                    SELECT id
                    FROM user_embeddings
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT -1 OFFSET ?
                    """,
                    (user_id, max_embeddings),
                ).fetchall()
                if overflow_rows:
                    conn.executemany(
                        "DELETE FROM user_embeddings WHERE id = ?",
                        [(row["id"],) for row in overflow_rows],
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        return self.get_user_by_id(user_id)

    def list_user_embeddings(self, user_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, embedding_json, created_at
                FROM user_embeddings
                WHERE user_id = ?
                ORDER BY id ASC
                """,
                (user_id,),
            ).fetchall()

        embeddings: list[dict[str, Any]] = []
        for row in rows:
            normalized = normalize_embedding_vector(json.loads(row["embedding_json"]))
            if normalized is None:
                continue
            embeddings.append(
                {
                    "user_embedding_id": str(row["id"]),
                    "user_id": user_id,
                    "embedding": normalized.astype(float).tolist(),
                    "created_at": row["created_at"],
                }
            )
        return embeddings

    def list_matches_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    user_id,
                    track_id,
                    camera_id,
                    video_id,
                    source_video_s3,
                    timestamp,
                    keyframe,
                    keyframe_s3,
                    score,
                    confidence,
                    distance,
                    embeddings_used,
                    distance_mean,
                    distance_std,
                    distance_max,
                    second_best_score,
                    score_margin,
                    created_at
                FROM matches
                WHERE user_id = ?
                ORDER BY datetime(created_at) DESC, id DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]
