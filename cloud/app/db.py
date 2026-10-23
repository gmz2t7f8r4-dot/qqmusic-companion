"""云端 SQLite：播放事件、最近状态、统计。七天以外的数据会自动删除。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterable

from .config import settings
from .timeutil import clamp_day_range, iso, now_iso, retention_start

SCHEMA = """
CREATE TABLE IF NOT EXISTS play_events (
    id TEXT PRIMARY KEY,
    device TEXT,
    title TEXT NOT NULL,
    artist TEXT,
    album TEXT,
    track_key TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    played_seconds REAL,
    duration_seconds REAL,
    completed INTEGER NOT NULL DEFAULT 1,
    end_reason TEXT,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_started ON play_events(started_at);
CREATE INDEX IF NOT EXISTS idx_events_track ON play_events(track_key);

CREATE TABLE IF NOT EXISTS collector_state (
    device TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    captured_at TEXT,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect() -> sqlite3.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def init() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        conn.commit()


# ---------- 上传 ----------
def insert_events(device: str, events: Iterable[dict]) -> tuple[int, int]:
    accepted = duplicates = 0
    received = now_iso()
    with db() as conn:
        for event in events:
            event_id = str(event.get("id") or "").strip()
            title = str(event.get("title") or "").strip()
            started_at = str(event.get("started_at") or "").strip()
            if not event_id or not title or not started_at:
                continue
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO play_events"
                    "(id, device, title, artist, album, track_key, started_at, ended_at, played_seconds,"
                    " duration_seconds, completed, end_reason, received_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        device,
                        title,
                        str(event.get("artist") or ""),
                        str(event.get("album") or ""),
                        str(event.get("track_key") or ""),
                        started_at,
                        str(event.get("ended_at") or ""),
                        float(event.get("played_seconds") or 0),
                        float(event.get("duration_seconds") or 0) or None,
                        1 if event.get("completed", True) else 0,
                        str(event.get("end_reason") or ""),
                        received,
                    ),
                )
                if cur.rowcount:
                    accepted += 1
                else:
                    duplicates += 1
            except (sqlite3.Error, TypeError, ValueError):
                continue
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('last_ingest_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (received,),
        )
        conn.commit()
    return accepted, duplicates


def save_state(device: str, payload: dict) -> None:
    received = now_iso()
    with db() as conn:
        conn.execute(
            "INSERT INTO collector_state(device, payload, captured_at, received_at) VALUES(?,?,?,?) "
            "ON CONFLICT(device) DO UPDATE SET payload=excluded.payload, "
            "captured_at=excluded.captured_at, received_at=excluded.received_at",
            (device, json.dumps(payload, ensure_ascii=False), str(payload.get("captured_at") or ""), received),
        )
        conn.commit()


def latest_state() -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT device, payload, captured_at, received_at FROM collector_state ORDER BY received_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return {
        "device": row["device"],
        "payload": json.loads(row["payload"]),
        "captured_at": row["captured_at"],
        "received_at": row["received_at"],
    }


# ---------- 查询 ----------
def query_events(
    day: date | None = None,
    day_from: date | None = None,
    day_to: date | None = None,
    title: str | None = None,
    event_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    start, end = clamp_day_range(day or day_from, day or day_to)
    sql = "SELECT * FROM play_events WHERE started_at >= ? AND started_at < ?"
    params: list = [iso(start), iso(end)]
    if title:
        sql += " AND (title LIKE ? OR artist LIKE ?)"
        params += [f"%{title}%", f"%{title}%"]
    if event_id:
        sql += " AND id = ?"
        params.append(event_id)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def top_songs(start: datetime, end: datetime, limit: int = 10) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT title, artist, album, track_key, COUNT(*) AS plays, "
            "SUM(COALESCE(played_seconds,0)) AS played_seconds, MAX(started_at) AS last_played_at "
            "FROM play_events WHERE started_at >= ? AND started_at < ? AND completed = 1 "
            "GROUP BY COALESCE(NULLIF(track_key,''), title || '|' || artist) "
            "ORDER BY plays DESC, played_seconds DESC LIMIT ?",
            (iso(start), iso(end), max(1, min(limit, 100))),
        ).fetchall()
    return [dict(row) for row in rows]


def song_plays(start: datetime, end: datetime, title: str | None = None, track_key: str | None = None) -> tuple[int, float]:
    sql = "SELECT COUNT(*) AS plays, SUM(COALESCE(played_seconds,0)) AS seconds FROM play_events " \
          "WHERE started_at >= ? AND started_at < ? AND completed = 1"
    params: list = [iso(start), iso(end)]
    if track_key:
        sql += " AND track_key = ?"
        params.append(track_key)
    if title:
        sql += " AND title LIKE ?"
        params.append(f"%{title}%")
    with db() as conn:
        row = conn.execute(sql, params).fetchone()
    return int(row["plays"] or 0), float(row["seconds"] or 0)


def daily_counts(start: datetime, end: datetime) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT substr(started_at, 1, 10) AS day, COUNT(*) AS plays, "
            "SUM(COALESCE(played_seconds,0)) AS played_seconds "
            "FROM play_events WHERE started_at >= ? AND started_at < ? AND completed = 1 "
            "GROUP BY day ORDER BY day",
            (iso(start), iso(end)),
        ).fetchall()
    return [dict(row) for row in rows]


# ---------- 清理 ----------
def cleanup_old_events() -> int:
    cutoff = iso(retention_start())
    with db() as conn:
        cur = conn.execute("DELETE FROM play_events WHERE started_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount


# ---------- 状态 ----------
def counts() -> dict:
    with db() as conn:
        events = conn.execute("SELECT COUNT(*) FROM play_events").fetchone()[0]
        last = conn.execute("SELECT MAX(started_at) FROM play_events").fetchone()[0]
        ingest = conn.execute("SELECT value FROM meta WHERE key='last_ingest_at'").fetchone()
    return {"events": int(events), "latest_event_at": last, "last_ingest_at": ingest[0] if ingest else None}


def health() -> tuple[bool, str | None]:
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
