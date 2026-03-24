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
                CREATE TABLE IF NOT EXISTS pools (
                    pool_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(created_by) REFERENCES users(user_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pools_created_by
                ON pools(created_by, created_at DESC)
                """
            )
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
            self._ensure_column(conn, "users", "role", "TEXT NOT NULL DEFAULT 'user'")
            self._ensure_column(conn, "users", "pool_id", "TEXT")
            self._create_index_if_columns(
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
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_embeddings_user_id
                ON user_embeddings(user_id, id DESC)
                """
            )
            self._ensure_column(conn, "user_embeddings", "source_image_s3", "TEXT")
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
                    best_similarity REAL,
                    second_best_similarity REAL,
                    margin REAL,
                    threshold_used REAL,
                    margin_threshold_used REAL,
                    decision_reason TEXT,
                    decision_explanation TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, track_id)
                )
                """
            )
            self._ensure_column(conn, "matches", "camera_id", "TEXT")
            self._ensure_column(conn, "matches", "video_id", "TEXT")
            self._ensure_column(conn, "matches", "source_video_s3", "TEXT")
            self._ensure_column(conn, "matches", "timestamp", "TEXT")
            self._ensure_column(conn, "matches", "keyframe", "TEXT")
            self._ensure_column(conn, "matches", "keyframe_s3", "TEXT")
            self._ensure_column(conn, "matches", "second_best_score", "REAL")
            self._ensure_column(conn, "matches", "score_margin", "REAL")
            self._ensure_column(conn, "matches", "pool_id", "TEXT")
            self._ensure_column(conn, "matches", "best_similarity", "REAL")
            self._ensure_column(conn, "matches", "second_best_similarity", "REAL")
            self._ensure_column(conn, "matches", "margin", "REAL")
            self._ensure_column(conn, "matches", "threshold_used", "REAL")
            self._ensure_column(conn, "matches", "margin_threshold_used", "REAL")
            self._ensure_column(conn, "matches", "decision_reason", "TEXT")
            self._ensure_column(conn, "matches", "decision_explanation", "TEXT")
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
            self._create_index_if_columns(
                conn,
                table_name="matches",
                required_columns=["pool_id", "track_id", "created_at"],
                create_sql="""
                CREATE INDEX IF NOT EXISTS idx_matches_pool_track
                ON matches(pool_id, track_id, created_at DESC)
                """,
            )
            self._create_index_if_columns(
                conn,
                table_name="matches",
                required_columns=["pool_id", "video_id", "created_at"],
                create_sql="""
                CREATE INDEX IF NOT EXISTS idx_matches_pool_video
                ON matches(pool_id, video_id, created_at DESC)
                """,
            )
        self._backfill_user_roles()

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
                                    user_id, email, password_hash, password_salt, role, pool_id, created_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    user_id,
                                    raw_user.get("email") or f"{user_id}@legacy.local",
                                    raw_user.get("password_hash"),
                                    raw_user.get("password_salt"),
                                    self._derive_role(
                                        raw_user.get("email") or f"{user_id}@legacy.local",
                                        prefer_first_admin=False,
                                    ),
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
            "role": row["role"],
            "pool_id": row["pool_id"],
            "pool": self.get_pool(row["pool_id"]),
            "created_at": row["created_at"],
            "embeddings": [json.loads(item["embedding_json"]) for item in embeddings_rows],
        }

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT user_id, email, password_hash, password_salt, role, pool_id, created_at
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
                SELECT user_id, email, password_hash, password_salt, role, pool_id, created_at
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

                role = self._derive_role(email, conn=conn)

                conn.execute(
                    """
                    INSERT INTO users (user_id, email, password_hash, password_salt, role, pool_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, email, password_hash, password_salt, role, None, created_at),
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
            "role": role,
            "pool_id": None,
            "pool": None,
            "created_at": created_at,
            "embeddings": [],
        }

    def append_user_embedding(
        self,
        user_id: str,
        embedding: list[float],
        source_image_s3: str | None = None,
        max_embeddings: int = 5,
    ) -> dict[str, Any] | None:
        created_at = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                user_row = conn.execute(
                    """
                    SELECT user_id, email, password_hash, password_salt, role, pool_id, created_at
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
                    INSERT INTO user_embeddings (user_id, embedding_json, source_image_s3, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, json.dumps(embedding), source_image_s3, created_at),
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

    def list_pools(self) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT pool_id, name, created_by, created_at, updated_at
                FROM pools
                ORDER BY datetime(created_at) ASC, pool_id ASC
                """
            ).fetchall()
        return [self._pool_from_row(row) for row in rows]

    def get_pool(self, pool_id: str | None) -> dict[str, Any] | None:
        if not pool_id:
            return None
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT pool_id, name, created_by, created_at, updated_at
                FROM pools
                WHERE pool_id = ?
                """,
                (pool_id,),
            ).fetchone()
        return self._pool_from_row(row) if row else None

    def create_pool(self, *, name: str, created_by: str) -> dict[str, Any]:
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("Pool name is required")

        pool_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT pool_id FROM pools WHERE lower(name) = lower(?)",
                    (normalized_name,),
                ).fetchone()
                if existing:
                    conn.rollback()
                    raise ValueError("Pool name already exists")
                conn.execute(
                    """
                    INSERT INTO pools (pool_id, name, created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (pool_id, normalized_name, created_by, now, now),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self.get_pool(pool_id) or {
            "pool_id": pool_id,
            "name": normalized_name,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }

    def update_user_pool(self, *, user_id: str, pool_id: str | None) -> dict[str, Any] | None:
        if pool_id is not None and not self.get_pool(pool_id):
            raise ValueError("Pool not found")
        with self.store.connection() as conn:
            conn.execute(
                """
                UPDATE users
                SET pool_id = ?
                WHERE user_id = ?
                """,
                (pool_id, user_id),
            )
        return self.get_user_by_id(user_id)

    def list_users(self, *, pool_id: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT user_id, email, password_hash, password_salt, role, pool_id, created_at
            FROM users
        """
        params: tuple[Any, ...] = ()
        if pool_id is not None:
            query += " WHERE pool_id = ?"
            params = (pool_id,)
        query += " ORDER BY datetime(created_at) ASC, user_id ASC"
        with self.store.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._user_from_row(row) for row in rows]

    def list_pool_reference_images(self, pool_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT ue.id, ue.user_id, u.email, ue.embedding_json, ue.source_image_s3, ue.created_at
                FROM user_embeddings ue
                INNER JOIN users u ON u.user_id = ue.user_id
                WHERE u.pool_id = ?
                ORDER BY datetime(ue.created_at) ASC, ue.id ASC
                """,
                (pool_id,),
            ).fetchall()

        images: list[dict[str, Any]] = []
        for row in rows:
            normalized = normalize_embedding_vector(json.loads(row["embedding_json"]))
            if normalized is None:
                continue
            images.append(
                {
                    "reference_image_id": str(row["id"]),
                    "user_embedding_id": str(row["id"]),
                    "user_id": row["user_id"],
                    "email": row["email"],
                    "embedding": normalized.astype(float).tolist(),
                    "source_image_s3": row["source_image_s3"],
                    "created_at": row["created_at"],
                }
            )
        return images

    def delete_user_embedding(self, *, user_id: str, embedding_id: str) -> bool:
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT id
                    FROM user_embeddings
                    WHERE user_id = ? AND id = ?
                    """,
                    (user_id, int(embedding_id)),
                ).fetchone()
                if not row:
                    conn.rollback()
                    return False
                conn.execute(
                    "DELETE FROM user_embeddings WHERE id = ?",
                    (int(embedding_id),),
                )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def list_user_embeddings(self, user_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, embedding_json, source_image_s3, created_at
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
                    "reference_image_id": str(row["id"]),
                    "user_embedding_id": str(row["id"]),
                    "user_id": user_id,
                    "embedding": normalized.astype(float).tolist(),
                    "source_image_s3": row["source_image_s3"],
                    "created_at": row["created_at"],
                }
            )
        return embeddings

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

    def list_matches_for_user(
        self,
        user_id: str,
        pool_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
                SELECT
                    id AS match_id,
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
                    best_similarity,
                    second_best_similarity,
                    margin,
                    threshold_used,
                    margin_threshold_used,
                    decision_reason,
                    decision_explanation,
                    pool_id,
                    created_at
                FROM matches
                WHERE user_id = ?
        """
        params: list[Any] = [user_id]
        if pool_id is not None:
            query += " AND pool_id = ?"
            params.append(pool_id)
        query += """
                ORDER BY datetime(created_at) DESC, id DESC
        """
        with self.store.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def list_matches_for_video(
        self,
        *,
        video_id: str,
        pool_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
                SELECT
                    m.id AS match_id,
                    m.user_id,
                    u.email,
                    u.pool_id,
                m.track_id,
                m.video_id,
                m.source_video_s3,
                m.timestamp,
                m.keyframe,
                m.keyframe_s3,
                m.score,
                m.confidence,
                m.distance,
                m.embeddings_used,
                m.distance_mean,
                m.distance_std,
                m.distance_max,
                m.second_best_score,
                m.score_margin,
                m.best_similarity,
                m.second_best_similarity,
                m.margin,
                m.threshold_used,
                m.margin_threshold_used,
                m.decision_reason,
                m.decision_explanation,
                m.pool_id AS match_pool_id,
                m.created_at
            FROM matches m
            INNER JOIN users u ON u.user_id = m.user_id
            WHERE m.video_id = ?
        """
        params: list[Any] = [video_id]
        if pool_id is not None:
            query += " AND u.pool_id = ?"
            params.append(pool_id)
        query += " ORDER BY m.score DESC, datetime(m.created_at) DESC, m.id DESC"
        with self.store.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def list_video_assignments(
        self,
        *,
        pool_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            self._ensure_column(conn, "matches", "pool_id", "TEXT")
        query = """
            SELECT DISTINCT
                m.video_id,
                m.user_id,
                u.email,
                u.pool_id,
                MAX(m.score) AS best_score,
                MAX(m.confidence) AS best_confidence
            FROM matches m
            INNER JOIN users u ON u.user_id = m.user_id
            WHERE m.video_id IS NOT NULL
        """
        params: list[Any] = []
        if pool_id is not None:
            query += " AND u.pool_id = ?"
            params.append(pool_id)
        query += " GROUP BY m.video_id, m.user_id, u.email, u.pool_id"
        with self.store.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def get_match_statistics(self, *, pool_id: str | None = None) -> dict[str, float | int]:
        query = """
            SELECT
                COUNT(*) AS total_matches,
                AVG(COALESCE(best_similarity, score)) AS average_similarity,
                AVG(margin) AS average_margin
            FROM matches
            WHERE 1 = 1
        """
        params: list[Any] = []
        if pool_id is not None:
            query += " AND pool_id = ?"
            params.append(pool_id)
        with self.store.connection() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        total_matches = 0 if row is None or row["total_matches"] is None else int(row["total_matches"])
        average_similarity = None if row is None else row["average_similarity"]
        average_margin = None if row is None else row["average_margin"]
        return {
            "total_matches": total_matches,
            "average_similarity": None if average_similarity is None else float(average_similarity),
            "average_margin": None if average_margin is None else float(average_margin),
        }

    def _pool_from_row(self, row) -> dict[str, Any]:
        return {
            "pool_id": row["pool_id"],
            "id": row["pool_id"],
            "name": row["name"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _admin_emails(self) -> set[str]:
        configured = os.environ.get("ADMIN_EMAILS", "").strip()
        if not configured:
            return set()
        return {
            item.strip().lower()
            for item in configured.split(",")
            if item.strip()
        }

    def _derive_role(
        self,
        email: str,
        *,
        conn=None,
        prefer_first_admin: bool = True,
    ) -> str:
        normalized_email = (email or "").strip().lower()
        admin_emails = self._admin_emails()
        if normalized_email and normalized_email in admin_emails:
            return "admin"
        if admin_emails or not prefer_first_admin:
            return "user"
        if conn is not None:
            existing_admin = conn.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
            ).fetchone()
            return "user" if existing_admin else "admin"
        with self.store.connection() as temporary_conn:
            existing_admin = temporary_conn.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
            ).fetchone()
        return "user" if existing_admin else "admin"

    def _backfill_user_roles(self) -> None:
        admin_emails = self._admin_emails()
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT user_id, email, role, created_at
                FROM users
                ORDER BY datetime(created_at) ASC, user_id ASC
                """
            ).fetchall()
            if not rows:
                return

            updates: list[tuple[str, str]] = []
            has_admin = False
            for row in rows:
                desired_role = "admin" if row["email"].strip().lower() in admin_emails else "user"
                if not admin_emails and not has_admin:
                    desired_role = "admin"
                has_admin = has_admin or desired_role == "admin"
                if row["role"] != desired_role:
                    updates.append((desired_role, row["user_id"]))

            if updates:
                conn.executemany(
                    "UPDATE users SET role = ? WHERE user_id = ?",
                    updates,
                )
