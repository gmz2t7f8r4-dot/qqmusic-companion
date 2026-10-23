"""本地 SQLite：待上传队列、时长缓存、运行状态。只存必要信息，不存歌词正文。"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOCAL_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS duration_cache (
    track_key TEXT PRIMARY KEY,
    duration REAL,
    source TEXT,
    found INTEGER NOT NULL DEFAULT 0,
    checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------- 待上传事件 ----------
    def enqueue_event(self, event: dict) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO events(id, payload, created_at) VALUES(?,?,?)",
                (event["id"], json.dumps(event, ensure_ascii=False), now_iso()),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def pending_events(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, payload, attempts FROM events ORDER BY created_at LIMIT ?", (limit,)
            ).fetchall()
        return [{"_id": r[0], "payload": json.loads(r[1]), "attempts": r[2]} for r in rows]

    def pending_count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def delete_events(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._lock:
            self._conn.executemany("DELETE FROM events WHERE id=?", [(i,) for i in ids])
            self._conn.commit()

    def mark_attempt(self, ids: list[str], error: str) -> None:
        if not ids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE events SET attempts=attempts+1, last_error=? WHERE id=?",
                [(error[:300], i) for i in ids],
            )
            self._conn.commit()

    # ---------- 时长缓存 ----------
    def cached_duration(self, track_key: str, max_age_days: int = 30) -> float | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT duration, found, checked_at FROM duration_cache WHERE track_key=?", (track_key,)
            ).fetchone()
        if not row:
            return None
        duration, found, checked_at = row
        try:
            checked = datetime.fromisoformat(checked_at)
        except ValueError:
            return None
        if datetime.now(LOCAL_TZ) - checked > timedelta(days=max_age_days):
            return None
        return float(duration) if found else None

    def cache_duration(self, track_key: str, duration: float | None, source: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO duration_cache(track_key, duration, source, found, checked_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(track_key) DO UPDATE SET duration=excluded.duration, source=excluded.source, "
                "found=excluded.found, checked_at=excluded.checked_at",
                (track_key, duration, source, 1 if duration else 0, now_iso()),
            )
            self._conn.commit()

    # ---------- 元数据 ----------
    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def close(self) -> None:
        with self._lock:
            self._conn.close()
