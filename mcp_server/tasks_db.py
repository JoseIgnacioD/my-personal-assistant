"""Local SQLite task list used by the Tasks agent's tools."""
import sqlite3
from contextlib import closing
from datetime import datetime

from config.settings import DATA_DIR

DB_PATH = DATA_DIR / "jarvis.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    due        TEXT,                 -- YYYY-MM-DD or NULL
    done       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
)
"""


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    return conn


def add_task(title: str, due: str | None = None) -> dict:
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, due, created_at) VALUES (?, ?, ?)",
            (title.strip(), due or None, datetime.now().isoformat(timespec="seconds")),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_tasks(include_done: bool = False) -> list[dict]:
    query = "SELECT * FROM tasks"
    if not include_done:
        query += " WHERE done = 0"
    query += " ORDER BY done, due IS NULL, due, id"
    with closing(_connect()) as conn:
        return [dict(r) for r in conn.execute(query).fetchall()]


def complete_task(task_id: int) -> dict | None:
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE tasks SET done = 1 WHERE id = ?", (task_id,))
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None
