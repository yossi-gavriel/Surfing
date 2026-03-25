import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np

from shared.utils.embeddings import normalize_embedding_vector, normalize_embeddings
from shared.utils.logger import get_logger
from shared.utils.sqlite_store import SQLiteStore, load_json_records

logger = get_logger("matching-db")


@dataclass(frozen=True)
class MatchWriteResult:
    status: str
    track_id: str
    user_id: str
    existing_user_id: str | None = None
    score_delta: float | None = None
    required_improvement: float | None = None


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
                    role TEXT NOT NULL DEFAULT 'user',
                    pool_id TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            self.store.ensure_column(conn, "users", "role", "TEXT NOT NULL DEFAULT 'user'")
            self.store.ensure_column(conn, "users", "pool_id", "TEXT")
            self.store.create_index_if_columns(
                conn,
                table_name="users",
                required_columns=["pool_id", "user_id"],
                create_sql="""
                CREATE INDEX IF NOT EXISTS idx_users_pool_id
                ON users(pool_id, user_id)
                """,
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    source_image_s3 TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
                """
            )
            self.store.ensure_column(conn, "user_embeddings", "source_image_s3", "TEXT")
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
                            user_id, email, password_hash, password_salt, role, pool_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user_id,
                            raw_user.get("email") or f"{user_id}@legacy.local",
                            raw_user.get("password_hash"),
                            raw_user.get("password_salt"),
                            raw_user.get("role") or "user",
                            raw_user.get("pool_id"),
                            created_at,
                        ),
                    )

                    embeddings = raw_user.get("embeddings")
                    if embeddings is None and raw_user.get("embedding") is not None:
                        embeddings = [raw_user["embedding"]]

                    for embedding in embeddings or []:
                        conn.execute(
                            """
                            INSERT INTO user_embeddings (user_id, embedding_json, source_image_s3, created_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (user_id, json.dumps(embedding), None, created_at),
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
                    u.email,
                    u.role,
                    u.pool_id,
                    ue.id AS embedding_id,
                    ue.embedding_json
                FROM users u
                LEFT JOIN user_embeddings ue ON ue.user_id = u.user_id
                ORDER BY u.user_id, ue.id ASC
                """
            ).fetchall()

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            user_id = row["user_id"]
            grouped.setdefault(
                user_id,
                {
                    "user_id": user_id,
                    "email": row["email"],
                    "role": row["role"],
                    "pool_id": row["pool_id"],
                    "records": [],
                },
            )
            if row["embedding_json"] is not None:
                grouped[user_id]["records"].append(
                    {
                        "embedding_id": str(row["embedding_id"]),
                        "embedding": json.loads(row["embedding_json"]),
                    }
                )

        loaded_users: list[dict[str, Any]] = []
        for user_id, grouped_user in grouped.items():
            raw_records = grouped_user["records"]
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
                    "email": grouped_user["email"],
                    "role": grouped_user["role"],
                    "pool_id": grouped_user["pool_id"],
                    "embeddings": embeddings,
                    "embedding_ids": embedding_ids,
                    "avg_embedding": avg_embedding[0],
                }
            )
        return loaded_users

    def get_all_users(self, pool_id: str | None = None) -> list[dict[str, Any]]:
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
            return self._filter_users_by_pool(self._cached_users, pool_id)

        self._cached_users = self._load_users()
        self._cache_key = cache_key
        return self._filter_users_by_pool(self._cached_users, pool_id)

    def _filter_users_by_pool(
        self,
        users: list[dict[str, Any]],
        pool_id: str | None,
    ) -> list[dict[str, Any]]:
        if pool_id is None:
            return users
        return [user for user in users if user.get("pool_id") == pool_id]


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
                    user_embedding_id TEXT,
                    video_embedding_id TEXT,
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
                    best_similarity REAL,
                    second_best_similarity REAL,
                    margin REAL,
                    threshold_used REAL,
                    margin_threshold_used REAL,
                    decision_reason TEXT,
                    decision_explanation TEXT,
                    top_candidates_json TEXT,
                    pool_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, track_id)
                )
                """
            )
            self.store.ensure_column(conn, "matches", "user_embedding_id", "TEXT")
            self.store.ensure_column(conn, "matches", "video_embedding_id", "TEXT")
            self.store.ensure_column(conn, "matches", "camera_id", "TEXT")
            self.store.ensure_column(conn, "matches", "video_id", "TEXT")
            self.store.ensure_column(conn, "matches", "source_video_s3", "TEXT")
            self.store.ensure_column(conn, "matches", "timestamp", "TEXT")
            self.store.ensure_column(conn, "matches", "keyframe", "TEXT")
            self.store.ensure_column(conn, "matches", "keyframe_s3", "TEXT")
            self.store.ensure_column(conn, "matches", "second_best_score", "REAL")
            self.store.ensure_column(conn, "matches", "score_margin", "REAL")
            self.store.ensure_column(conn, "matches", "pool_id", "TEXT")
            self.store.ensure_column(conn, "matches", "best_similarity", "REAL")
            self.store.ensure_column(conn, "matches", "second_best_similarity", "REAL")
            self.store.ensure_column(conn, "matches", "margin", "REAL")
            self.store.ensure_column(conn, "matches", "threshold_used", "REAL")
            self.store.ensure_column(conn, "matches", "margin_threshold_used", "REAL")
            self.store.ensure_column(conn, "matches", "decision_reason", "TEXT")
            self.store.ensure_column(conn, "matches", "decision_explanation", "TEXT")
            self.store.ensure_column(conn, "matches", "top_candidates_json", "TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_matches_user_created
                ON matches(user_id, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_matches_track_id
                ON matches(track_id, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_matches_track_id
                ON matches(track_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_matches_video_id
                ON matches(video_id, created_at DESC)
                """
            )
            self.store.create_index_if_columns(
                conn,
                table_name="matches",
                required_columns=["pool_id", "track_id", "created_at"],
                create_sql="""
                CREATE INDEX IF NOT EXISTS idx_matches_pool_track
                ON matches(pool_id, track_id, created_at DESC)
                """,
            )
            self.store.create_index_if_columns(
                conn,
                table_name="matches",
                required_columns=["pool_id", "video_id", "created_at"],
                create_sql="""
                CREATE INDEX IF NOT EXISTS idx_matches_pool_video
                ON matches(pool_id, video_id, created_at DESC)
                """,
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
                            second_best_score, score_margin, best_similarity, second_best_similarity,
                            margin, threshold_used, margin_threshold_used, decision_reason,
                            decision_explanation, pool_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            match.get("best_similarity", match.get("score")),
                            match.get("second_best_similarity", match.get("second_best_score")),
                            match.get("margin", match.get("score_margin")),
                            match.get("threshold_used"),
                            match.get("margin_threshold_used"),
                            match.get("decision_reason"),
                            match.get("decision_explanation"),
                            match.get("pool_id"),
                            match.get("created_at") or datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def add_match(
        self,
        match: dict[str, Any],
        *,
        significant_improvement_margin: float = 0.05,
    ) -> MatchWriteResult:
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        COALESCE(best_similarity, score) AS best_similarity
                    FROM matches
                    WHERE track_id = ?
                    ORDER BY datetime(created_at) DESC, id DESC
                    LIMIT 1
                    """,
                    (match["track_id"],),
                ).fetchone()
                if existing:
                    existing_user_id = str(existing["user_id"])
                    if existing_user_id == str(match["user_id"]):
                        conn.rollback()
                        return MatchWriteResult(
                            status="duplicate",
                            track_id=str(match["track_id"]),
                            user_id=str(match["user_id"]),
                            existing_user_id=existing_user_id,
                        )

                    existing_score = float(existing["best_similarity"] or 0.0)
                    incoming_score = float(match.get("best_similarity", match["score"]))
                    required_improvement = max(float(significant_improvement_margin), 0.03)
                    score_delta = incoming_score - existing_score
                    if score_delta < required_improvement:
                        conn.rollback()
                        return MatchWriteResult(
                            status="retained_existing",
                            track_id=str(match["track_id"]),
                            user_id=str(match["user_id"]),
                            existing_user_id=existing_user_id,
                            score_delta=score_delta,
                            required_improvement=required_improvement,
                        )

                    self._update_match_row(conn, row_id=int(existing["id"]), match=match)
                    conn.commit()
                    return MatchWriteResult(
                        status="reassigned",
                        track_id=str(match["track_id"]),
                        user_id=str(match["user_id"]),
                        existing_user_id=existing_user_id,
                        score_delta=score_delta,
                        required_improvement=required_improvement,
                    )

                self._insert_match_row(conn, match=match)
                conn.commit()
                return MatchWriteResult(
                    status="inserted",
                    track_id=str(match["track_id"]),
                    user_id=str(match["user_id"]),
                )
            except Exception:
                conn.rollback()
                raise

    def _insert_match_row(self, conn, *, match: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO matches (
                user_id, user_embedding_id, video_embedding_id, track_id, camera_id, video_id, source_video_s3,
                timestamp, keyframe, keyframe_s3, score, confidence, distance,
                embeddings_used, distance_mean, distance_std, distance_max,
                second_best_score, score_margin, best_similarity, second_best_similarity,
                margin, threshold_used, margin_threshold_used, decision_reason,
                decision_explanation, top_candidates_json, pool_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._match_row_values(match),
        )

    def _update_match_row(self, conn, *, row_id: int, match: dict[str, Any]) -> None:
        values = self._match_row_values(match)
        conn.execute(
            """
            UPDATE matches
            SET user_id = ?, user_embedding_id = ?, video_embedding_id = ?, track_id = ?, camera_id = ?, video_id = ?, source_video_s3 = ?,
                timestamp = ?, keyframe = ?, keyframe_s3 = ?, score = ?, confidence = ?, distance = ?,
                embeddings_used = ?, distance_mean = ?, distance_std = ?, distance_max = ?,
                second_best_score = ?, score_margin = ?, best_similarity = ?, second_best_similarity = ?,
                margin = ?, threshold_used = ?, margin_threshold_used = ?, decision_reason = ?,
                decision_explanation = ?, top_candidates_json = ?, pool_id = ?, created_at = ?
            WHERE id = ?
            """,
            (*values, row_id),
        )

    def _match_row_values(self, match: dict[str, Any]) -> tuple[Any, ...]:
        return (
            match["user_id"],
            match.get("user_embedding_id"),
            match.get("video_embedding_id"),
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
            match.get("best_similarity", match["score"]),
            match.get("second_best_similarity", match.get("second_best_score")),
            match.get("margin", match.get("score_margin")),
            match.get("threshold_used"),
            match.get("margin_threshold_used"),
            match.get("decision_reason"),
            match.get("decision_explanation"),
            None
            if match.get("top_candidates") is None
            else json.dumps(match.get("top_candidates")),
            match.get("pool_id"),
            match.get("created_at") or datetime.now(timezone.utc).isoformat(),
        )
