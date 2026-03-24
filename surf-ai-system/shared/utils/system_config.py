from __future__ import annotations

import math
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from shared.utils.sqlite_store import SQLiteStore


SYSTEM_CONFIG_DEFINITIONS: dict[str, dict[str, Any]] = {
    "min_similarity": {"default": 0.75, "type": "float", "min": 0.5, "max": 0.95, "step": 0.01},
    "min_margin": {"default": 0.05, "type": "float", "min": 0.01, "max": 0.2, "step": 0.01},
    "min_frames_per_track": {"default": 3, "type": "int", "min": 2, "max": 10, "step": 1},
    "top_k_embeddings": {"default": 5, "type": "int", "min": 1, "max": 10, "step": 1},
    "min_quality_score": {"default": 0.5, "type": "float", "min": 0.1, "max": 1.0, "step": 0.01},
    "retention_days": {"default": 7, "type": "int", "min": 1, "max": 30, "step": 1},
}


def get_default_system_config() -> dict[str, int | float]:
    return {
        key: definition["default"]
        for key, definition in SYSTEM_CONFIG_DEFINITIONS.items()
    }


def get_system_config_metadata() -> dict[str, dict[str, Any]]:
    return {
        key: {
            "type": definition["type"],
            "default": definition["default"],
            "min": definition["min"],
            "max": definition["max"],
            "step": definition["step"],
        }
        for key, definition in SYSTEM_CONFIG_DEFINITIONS.items()
    }


def _coerce_config_value(key: str, value: Any) -> int | float:
    definition = SYSTEM_CONFIG_DEFINITIONS.get(key)
    if definition is None:
        raise KeyError(f"Unsupported config key: {key}")

    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric value for {key}") from exc

    if not math.isfinite(numeric_value):
        raise ValueError(f"Invalid numeric value for {key}")

    min_value = float(definition["min"])
    max_value = float(definition["max"])
    if numeric_value < min_value or numeric_value > max_value:
        raise ValueError(f"{key} must be between {min_value} and {max_value}")

    if definition["type"] == "int":
        rounded_value = int(round(numeric_value))
        if not math.isclose(numeric_value, float(rounded_value), abs_tol=1e-9):
            raise ValueError(f"{key} must be a whole number")
        return rounded_value
    return float(numeric_value)


class SystemConfigService:
    def __init__(self, db_path: str, *, cache_ttl_seconds: int = 30):
        self.store = SQLiteStore(db_path)
        self.cache_ttl_seconds = max(int(cache_ttl_seconds), 1)
        self.cooldown_seconds = max(int(os.environ.get("SYSTEM_CONFIG_COOLDOWN_SECONDS", "10")), 0)
        self._cache_lock = threading.Lock()
        self._cache_expires_at = 0.0
        self._cached_records: dict[str, dict[str, Any]] = {}
        self._create_schema()
        self._seed_defaults()

    def get_config(self, key: str, default: int | float | None = None) -> int | float:
        records = self._get_cached_records()
        if key in records:
            return records[key]["value"]

        definition = SYSTEM_CONFIG_DEFINITIONS.get(key)
        if definition is not None:
            return definition["default"]
        if default is None:
            raise KeyError(f"Unsupported config key: {key}")
        return default

    def get_all_config(self) -> dict[str, int | float]:
        records = self._get_cached_records()
        defaults = get_default_system_config()
        merged = {
            key: records.get(key, {}).get("value", default_value)
            for key, default_value in defaults.items()
        }
        for key, record in records.items():
            merged[key] = record["value"]
        return merged

    def get_all_config_records(self) -> dict[str, dict[str, Any]]:
        records = self._get_cached_records()
        defaults = get_default_system_config()
        merged: dict[str, dict[str, Any]] = {}
        for key, default_value in defaults.items():
            record = records.get(key)
            merged[key] = {
                "key": key,
                "value": default_value if record is None else record["value"],
                "updated_at": None if record is None else record["updated_at"],
                "updated_by": None if record is None else record["updated_by"],
            }
        return merged

    def update_config(
        self,
        values: dict[str, Any],
        *,
        updated_by: str,
        admin_id: str,
        change_reason: str = "update",
    ) -> dict[str, int | float]:
        normalized_updates: dict[str, int | float] = {}
        for key, value in values.items():
            normalized_updates[key] = _coerce_config_value(key, value)

        if self.cooldown_seconds and change_reason != "rollback":
            guard_state = self.get_update_guard_state()
            if int(guard_state["cooldown_remaining_seconds"]) > 0:
                latest_change = guard_state.get("latest_change")
                changed_at = None if not latest_change else latest_change.get("changed_at")
                raise ValueError(
                    "Config was updated recently"
                    if not changed_at
                    else f"Config was updated recently at {changed_at}. Please wait before changing it again."
                )

        now = datetime.now(timezone.utc).isoformat()
        batch_id = str(uuid.uuid4())
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for key, value in normalized_updates.items():
                    existing = conn.execute(
                        """
                        SELECT value
                        FROM system_config
                        WHERE key = ?
                        """,
                        (key,),
                    ).fetchone()
                    old_value = (
                        float(existing["value"])
                        if existing is not None
                        else float(SYSTEM_CONFIG_DEFINITIONS[key]["default"])
                    )
                    if math.isclose(old_value, float(value), abs_tol=1e-9):
                        continue
                    conn.execute(
                        """
                        INSERT INTO system_config (key, value, updated_at, updated_by)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            value = excluded.value,
                            updated_at = excluded.updated_at,
                            updated_by = excluded.updated_by
                        """,
                        (key, float(value), now, updated_by),
                    )
                    conn.execute(
                        """
                        INSERT INTO system_config_audit (
                            batch_id,
                            key,
                            old_value,
                            new_value,
                            changed_at,
                            admin_id,
                            updated_by,
                            change_reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            batch_id,
                            key,
                            old_value,
                            float(value),
                            now,
                            admin_id,
                            updated_by,
                            change_reason,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        self.invalidate_cache()
        return self.get_all_config()

    def rollback_config(
        self,
        *,
        updated_by: str,
        admin_id: str,
        batch_id: str | None = None,
        audit_id: int | None = None,
        key: str | None = None,
    ) -> dict[str, Any]:
        target_rows = self._resolve_rollback_rows(
            batch_id=batch_id,
            audit_id=audit_id,
            key=key,
        )
        rollback_values = {
            row["key"]: (
                int(round(float(row["old_value"])))
                if SYSTEM_CONFIG_DEFINITIONS[row["key"]]["type"] == "int"
                else float(row["old_value"])
            )
            for row in target_rows
        }
        current_config = self.update_config(
            rollback_values,
            updated_by=updated_by,
            admin_id=admin_id,
            change_reason="rollback",
        )
        return {
            "config": current_config,
            "rolled_back": [
                {
                    "audit_id": int(row["audit_id"]),
                    "batch_id": row["batch_id"],
                    "key": row["key"],
                    "old_value": self._typed_value(row["key"], row["old_value"]),
                    "new_value": self._typed_value(row["key"], row["new_value"]),
                    "changed_at": row["changed_at"],
                    "admin_id": row["admin_id"],
                }
                for row in target_rows
            ],
        }

    def list_change_history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    audit_id,
                    batch_id,
                    key,
                    old_value,
                    new_value,
                    changed_at,
                    admin_id,
                    updated_by,
                    change_reason
                FROM system_config_audit
                ORDER BY datetime(changed_at) DESC, audit_id DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [
            {
                "audit_id": int(row["audit_id"]),
                "batch_id": row["batch_id"],
                "key": row["key"],
                "old_value": self._typed_value(row["key"], row["old_value"]),
                "new_value": self._typed_value(row["key"], row["new_value"]),
                "changed_at": row["changed_at"],
                "admin_id": row["admin_id"],
                "updated_by": row["updated_by"],
                "change_reason": row["change_reason"],
            }
            for row in rows
        ]

    def get_update_guard_state(self) -> dict[str, Any]:
        latest_change = self._latest_change()
        remaining_seconds = 0
        if latest_change is not None and self.cooldown_seconds:
            changed_at = datetime.fromisoformat(latest_change["changed_at"])
            elapsed_seconds = max(
                0,
                int((datetime.now(timezone.utc) - changed_at).total_seconds()),
            )
            remaining_seconds = max(self.cooldown_seconds - elapsed_seconds, 0)

        return {
            "cooldown_seconds": self.cooldown_seconds,
            "cooldown_remaining_seconds": remaining_seconds,
            "latest_change": latest_change,
        }

    def invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache_expires_at = 0.0
            self._cached_records = {}

    def _create_schema(self) -> None:
        with self.store.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY,
                    value REAL NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_system_config_updated_at
                ON system_config(datetime(updated_at) DESC, key)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_config_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    old_value REAL NOT NULL,
                    new_value REAL NOT NULL,
                    changed_at TEXT NOT NULL,
                    admin_id TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    change_reason TEXT NOT NULL DEFAULT 'update'
                )
                """
            )
            self._ensure_column(conn, "system_config_audit", "change_reason", "TEXT NOT NULL DEFAULT 'update'")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_system_config_audit_changed_at
                ON system_config_audit(datetime(changed_at) DESC, audit_id DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_system_config_audit_batch
                ON system_config_audit(batch_id, audit_id)
                """
            )

    def _seed_defaults(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        defaults = get_default_system_config()
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for key, value in defaults.items():
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO system_config (key, value, updated_at, updated_by)
                        VALUES (?, ?, ?, ?)
                        """,
                        (key, float(value), now, "system"),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _get_cached_records(self) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        with self._cache_lock:
            if now < self._cache_expires_at and self._cached_records:
                return dict(self._cached_records)

        records = self._load_records()
        with self._cache_lock:
            self._cached_records = records
            self._cache_expires_at = time.monotonic() + self.cache_ttl_seconds
            return dict(self._cached_records)

    def _load_records(self) -> dict[str, dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT key, value, updated_at, updated_by
                FROM system_config
                """
            ).fetchall()

        records: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = row["key"]
            definition = SYSTEM_CONFIG_DEFINITIONS.get(key)
            if definition is None:
                continue
            typed_value: int | float
            if definition["type"] == "int":
                typed_value = int(round(float(row["value"])))
            else:
                typed_value = float(row["value"])
            records[key] = {
                "key": key,
                "value": typed_value,
                "updated_at": row["updated_at"],
                "updated_by": row["updated_by"],
            }
        return records

    def _resolve_rollback_rows(
        self,
        *,
        batch_id: str | None,
        audit_id: int | None,
        key: str | None,
    ):
        with self.store.connection() as conn:
            if audit_id is not None:
                rows = conn.execute(
                    """
                    SELECT
                        audit_id,
                        batch_id,
                        key,
                        old_value,
                        new_value,
                        changed_at,
                        admin_id
                    FROM system_config_audit
                    WHERE audit_id = ?
                    ORDER BY audit_id ASC
                    """,
                    (int(audit_id),),
                ).fetchall()
            elif batch_id:
                rows = conn.execute(
                    """
                    SELECT
                        audit_id,
                        batch_id,
                        key,
                        old_value,
                        new_value,
                        changed_at,
                        admin_id
                    FROM system_config_audit
                    WHERE batch_id = ?
                    ORDER BY audit_id ASC
                    """,
                    (batch_id,),
                ).fetchall()
            elif key:
                rows = conn.execute(
                    """
                    SELECT
                        audit_id,
                        batch_id,
                        key,
                        old_value,
                        new_value,
                        changed_at,
                        admin_id
                    FROM system_config_audit
                    WHERE key = ?
                    ORDER BY datetime(changed_at) DESC, audit_id DESC
                    LIMIT 1
                    """,
                    (key,),
                ).fetchall()
            else:
                latest = conn.execute(
                    """
                    SELECT batch_id
                    FROM system_config_audit
                    ORDER BY datetime(changed_at) DESC, audit_id DESC
                    LIMIT 1
                    """
                ).fetchone()
                if latest is None:
                    rows = []
                else:
                    rows = conn.execute(
                        """
                        SELECT
                            audit_id,
                            batch_id,
                            key,
                            old_value,
                            new_value,
                            changed_at,
                            admin_id
                        FROM system_config_audit
                        WHERE batch_id = ?
                        ORDER BY audit_id ASC
                        """,
                        (latest["batch_id"],),
                    ).fetchall()

        if not rows:
            raise ValueError("No config changes are available to roll back")
        return rows

    def _latest_change(self) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    audit_id,
                    batch_id,
                    key,
                    old_value,
                    new_value,
                    changed_at,
                    admin_id,
                    updated_by,
                    change_reason
                FROM system_config_audit
                ORDER BY datetime(changed_at) DESC, audit_id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return {
            "audit_id": int(row["audit_id"]),
            "batch_id": row["batch_id"],
            "key": row["key"],
            "old_value": self._typed_value(row["key"], row["old_value"]),
            "new_value": self._typed_value(row["key"], row["new_value"]),
            "changed_at": row["changed_at"],
            "admin_id": row["admin_id"],
            "updated_by": row["updated_by"],
            "change_reason": row["change_reason"],
        }

    def _typed_value(self, key: str, value: Any) -> int | float:
        definition = SYSTEM_CONFIG_DEFINITIONS[key]
        if definition["type"] == "int":
            return int(round(float(value)))
        return float(value)

    def _ensure_column(self, conn, table_name: str, column_name: str, column_definition: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {row["name"] for row in rows}
        if column_name not in existing_columns:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )
