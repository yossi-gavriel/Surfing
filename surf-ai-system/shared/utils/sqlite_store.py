import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator


class SQLiteStore:
    _init_lock = threading.Lock()

    def __init__(self, db_path: str):
        self.db_path = db_path
        directory = os.path.dirname(db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with self._init_lock:
            with self.connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def table_has_rows(self, table_name: str) -> bool:
        with self.connection() as conn:
            row = conn.execute(f"SELECT 1 FROM {table_name} LIMIT 1").fetchone()
            return row is not None

    def table_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {row["name"] for row in rows}

    def ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> bool:
        existing_columns = self.table_columns(conn, table_name)
        if column_name in existing_columns:
            return False
        conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )
        return True

    def create_index_if_columns(
        self,
        conn: sqlite3.Connection,
        *,
        table_name: str,
        required_columns: list[str],
        create_sql: str,
    ) -> bool:
        existing_columns = self.table_columns(conn, table_name)
        if not set(required_columns).issubset(existing_columns):
            return False
        conn.execute(create_sql)
        return True


def load_json_records(path: str, root_key: str) -> list[dict]:
    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data.get(root_key, [])
