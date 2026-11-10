"""Local SQLite record of each request and the lines the agent wrote for it."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .lines import Print

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  session_id TEXT, turn_id TEXT, ts REAL, text TEXT, model TEXT, platform TEXT,
  PRIMARY KEY (session_id, turn_id));
CREATE TABLE IF NOT EXISTS edits (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, session_id TEXT, turn_id TEXT,
  path TEXT, tool TEXT, request TEXT, model TEXT);
CREATE TABLE IF NOT EXISTS lines (
  edit_id INTEGER, path TEXT, hash TEXT, prev TEXT, next TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS lines_by_path_hash ON lines (path, hash);
CREATE INDEX IF NOT EXISTS edits_by_path ON edits (path);
"""


def key(path: str) -> str:
    """One spelling per file: absolute, normalised, case-folded on Windows."""
    return os.path.normcase(os.path.abspath(path))


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # writing -------------------------------------------------------------

    def save_request(self, session_id: str, turn_id: str, text: str, model: str = "",
                     platform: str = "") -> None:
        with _LOCK:
            self._db.execute("INSERT OR REPLACE INTO requests VALUES (?,?,?,?,?,?)",
                             (session_id, turn_id, time.time(), text, model, platform))
            self._db.commit()

    def request_for(self, session_id: str, turn_id: str) -> tuple[str, str]:
        """The request (text, model) for this turn, or the latest one in the session."""
        with _LOCK:
            row = self._db.execute("SELECT text, model FROM requests WHERE session_id=? AND turn_id=?",
                                   (session_id, turn_id)).fetchone()
            if row is None:
                row = self._db.execute("SELECT text, model FROM requests WHERE session_id=? "
                                       "ORDER BY ts DESC LIMIT 1", (session_id,)).fetchone()
        return (row[0], row[1] or "") if row else ("", "")

    def save_edit(self, *, session_id: str, turn_id: str, path: str, tool: str,
                  written: list[Print]) -> Optional[int]:
        if not written:
            return None
        request, model = self.request_for(session_id, turn_id)
        with _LOCK:
            cur = self._db.execute(
                "INSERT INTO edits (ts, session_id, turn_id, path, tool, request, model) "
                "VALUES (?,?,?,?,?,?,?)",
                (time.time(), session_id, turn_id, key(path), tool, request, model))
            edit_id = cur.lastrowid
            self._db.executemany("INSERT INTO lines VALUES (?,?,?,?,?,?)",
                                 [(edit_id, key(path), p.hash, p.prev, p.next, p.text) for p in written])
            self._db.commit()
        return edit_id

    # reading -------------------------------------------------------------

    def candidates(self, path: str, line_hash: str) -> list[tuple[int, Print]]:
        with _LOCK:
            rows = self._db.execute("SELECT edit_id, hash, prev, next, text FROM lines "
                                    "WHERE path=? AND hash=? ORDER BY edit_id", (key(path), line_hash)).fetchall()
        return [(r[0], Print(r[1], r[2], r[3], r[4])) for r in rows]

    def edit(self, edit_id: int) -> Optional[dict[str, Any]]:
        with _LOCK:
            row = self._db.execute("SELECT id, ts, session_id, turn_id, path, tool, request, model "
                                   "FROM edits WHERE id=?", (edit_id,)).fetchone()
        if row is None:
            return None
        names = ("id", "ts", "session_id", "turn_id", "path", "tool", "request", "model")
        return dict(zip(names, row))

    def files_in_turn(self, session_id: str, turn_id: str) -> list[str]:
        with _LOCK:
            rows = self._db.execute("SELECT DISTINCT path FROM edits WHERE session_id=? AND turn_id=?",
                                    (session_id, turn_id)).fetchall()
        return [r[0] for r in rows]

    def known_paths(self) -> list[str]:
        with _LOCK:
            return [r[0] for r in self._db.execute("SELECT DISTINCT path FROM edits").fetchall()]

    def recent(self, limit: int = 5) -> list[dict[str, Any]]:
        """The latest requests that changed files, newest first, with the files they changed."""
        with _LOCK:
            rows = self._db.execute(
                "SELECT session_id, turn_id, MAX(ts), request, GROUP_CONCAT(DISTINCT path) "
                "FROM edits GROUP BY session_id, turn_id ORDER BY MAX(ts) DESC LIMIT ?", (limit,)).fetchall()
        return [dict(session_id=r[0], turn_id=r[1], ts=r[2], request=r[3], paths=(r[4] or "").split(","))
                for r in rows]

    def counts(self) -> tuple[int, int, int]:
        with _LOCK:
            edits = self._db.execute("SELECT COUNT(*) FROM edits").fetchone()[0]
            lines = self._db.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
            files = self._db.execute("SELECT COUNT(DISTINCT path) FROM edits").fetchone()[0]
        return edits, lines, files
