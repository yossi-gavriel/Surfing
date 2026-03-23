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


def load_json_records(path: str, root_key: str) -> list[dict]:
    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data.get(root_key, [])
