import json
import os
from datetime import datetime, timezone
from typing import Any

import numpy as np

from shared.utils.embeddings import normalize_embedding_vector, normalize_embeddings
from shared.utils.logger import get_logger
from shared.utils.sqlite_store import SQLiteStore, load_json_records

logger = get_logger("matching-db")


class UsersDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.store = SQLiteStore(db_path)
        self.legacy_users_file = os.path.join(os.path.dirname(db_path), "users.json")
        self._cache_key: tuple[float, int] | None = None
        self._cached_users: list[dict[str, Any]] = []
        self._create_schema()
        self._migrate_legacy_users()

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

    def _migrate_legacy_users(self) -> None:
        if self.store.table_has_rows("users"):
            return

        users = load_json_records(self.legacy_users_file, "users")
        if not users:
            return

        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for raw_user in users:
                    user_id = raw_user.get("user_id")
                    if not user_id:
                        continue

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

    def _load_users(self) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    u.user_id,
                    ue.id AS embedding_id,
                    ue.embedding_json
                FROM users u
                LEFT JOIN user_embeddings ue ON ue.user_id = u.user_id
                ORDER BY u.user_id, ue.id ASC
                """
            ).fetchall()

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            user_id = row["user_id"]
            grouped.setdefault(user_id, [])
            if row["embedding_json"] is not None:
                grouped[user_id].append(
                    {
                        "embedding_id": str(row["embedding_id"]),
                        "embedding": json.loads(row["embedding_json"]),
                    }
                )

        loaded_users: list[dict[str, Any]] = []
        for user_id, raw_records in grouped.items():
            if not raw_records:
                continue

            embedding_ids: list[str] = []
            normalized_rows: list[np.ndarray] = []
            for record in raw_records:
                normalized = normalize_embedding_vector(record["embedding"])
                if normalized is None:
                    continue
                embedding_ids.append(record["embedding_id"])
                normalized_rows.append(normalized)

            if not normalized_rows:
                continue

            embeddings = np.vstack(normalized_rows).astype(np.float32)
            avg_embedding = normalize_embeddings(np.mean(embeddings, axis=0))
            if avg_embedding.size == 0:
                continue

            loaded_users.append(
                {
                    "user_id": user_id,
                    "embeddings": embeddings,
                    "embedding_ids": embedding_ids,
                    "avg_embedding": avg_embedding[0],
                }
            )
        return loaded_users

    def get_all_users(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.db_path):
            logger.warning("Users database missing at %s", self.db_path)
            self._cached_users = []
            self._cache_key = None
            return []

        stat = os.stat(self.db_path)
        wal_path = f"{self.db_path}-wal"
        wal_stat = os.stat(wal_path) if os.path.exists(wal_path) else None
        cache_key = (
            stat.st_mtime,
            stat.st_size,
            wal_stat.st_mtime if wal_stat else 0.0,
            wal_stat.st_size if wal_stat else 0,
        )
        if self._cache_key == cache_key:
            return self._cached_users

        self._cached_users = self._load_users()
        self._cache_key = cache_key
        return self._cached_users


class MatchesDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.store = SQLiteStore(db_path)
        self.legacy_matches_file = os.path.join(os.path.dirname(db_path), "matches.json")
        self._create_schema()
        self._migrate_legacy_matches()

    def _create_schema(self) -> None:
        with self.store.connection() as conn:
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

    def _migrate_legacy_matches(self) -> None:
        if self.store.table_has_rows("matches"):
            return

        matches = load_json_records(self.legacy_matches_file, "matches")
        if not matches:
            return

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

    def add_match(self, match: dict[str, Any]) -> bool:
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    """
                    SELECT 1
                    FROM matches
                    WHERE user_id = ? AND track_id = ?
                    """,
                    (match["user_id"], match["track_id"]),
                ).fetchone()
                if existing:
                    conn.rollback()
                    return False

                conn.execute(
                    """
                    INSERT INTO matches (
                        user_id, track_id, camera_id, video_id, source_video_s3,
                        timestamp, keyframe, keyframe_s3, score, confidence, distance,
                        embeddings_used, distance_mean, distance_std, distance_max,
                        second_best_score, score_margin, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        match["user_id"],
                        match["track_id"],
                        match.get("camera_id"),
                        match.get("video_id"),
                        match.get("source_video_s3"),
                        match.get("timestamp"),
                        match.get("keyframe"),
                        match.get("keyframe_s3"),
                        float(match["score"]),
                        float(match["confidence"]),
                        float(match["distance"]),
                        int(match["embeddings_used"]),
                        float(match["distance_mean"]),
                        float(match["distance_std"]),
                        float(match["distance_max"]),
                        match.get("second_best_score"),
                        match.get("score_margin"),
                        match.get("created_at") or datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise
