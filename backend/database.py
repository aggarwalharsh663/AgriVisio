"""Small SQLite store for completed scan batches."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


@contextmanager
def _connect(database_path: Path) -> Generator[sqlite3.Connection, None, None]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.execute("PRAGMA synchronous=NORMAL;")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS batches (
                batch_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                batch_json TEXT NOT NULL
            )
            """
        )
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def save_batch(batch: dict, database_path: Path) -> None:
    """Insert or replace one complete batch record."""
    with _connect(database_path) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO batches (batch_id, created_at, batch_json)
            VALUES (?, ?, ?)
            """,
            (
                batch["batch_id"],
                batch["created_at"],
                json.dumps(batch, separators=(",", ":")),
            ),
        )


def get_batch(batch_id: str, database_path: Path) -> dict | None:
    """Read a batch by its public identifier."""
    if not database_path.is_file():
        return None
    with _connect(database_path) as connection:
        row = connection.execute(
            "SELECT batch_json FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["batch_json"])
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def list_batches(database_path: Path) -> list[dict]:
    """Return saved batches newest first, safely ignoring any corrupt records."""
    if not database_path.is_file():
        return []
    with _connect(database_path) as connection:
        rows = connection.execute(
            "SELECT batch_json FROM batches ORDER BY created_at DESC, batch_id DESC"
        ).fetchall()
    results = []
    for row in rows:
        try:
            parsed = json.loads(row["batch_json"])
            if isinstance(parsed, dict):
                results.append(parsed)
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
    return results


def list_batch_ids(database_path: Path, prefix: str = "") -> list[str]:
    """Return matching batch ids without deserializing entire records."""
    if not database_path.is_file():
        return []
    with _connect(database_path) as connection:
        if prefix:
            rows = connection.execute(
                "SELECT batch_id FROM batches WHERE batch_id LIKE ?", (f"{prefix}%",)
            ).fetchall()
        else:
            rows = connection.execute("SELECT batch_id FROM batches").fetchall()
    return [row["batch_id"] for row in rows]