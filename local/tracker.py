"""把“每秒读到的一次状态”变成“一条完整的播放事件”。

规则（按你的要求）：
- 只有整首听完才生成记录：累计播放时间 >= 时长 x 95%，并且这首歌确实是结束（切到下一首 / 播完停住）。
- 听一半、误点、切歌一律不记。
- 暂停不累加时间；电脑休眠/采集中断的那段不算听歌时间。
- 查不到时长的歌不记，只计数，之后在“服务状态”里能查。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from lrclib_duration import lookup_duration
from media_session import Snapshot
from store import Store

log = logging.getLogger("collector.tracker")
LOCAL_TZ = timezone(timedelta(hours=8))
VERSION = "1.0.0"

# 至少听了这么久才值得统计“因时长未知被漏掉”
UNKNOWN_DURATION_MIN_PLAY = 30.0


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, LOCAL_TZ).isoformat(timespec="seconds")


@dataclass
class Track:
    key: str
    title: str
    artist: str
    album: str
    started_at: float
    played: float = 0.0
    duration: float | None = None
    duration_source: str = "pending"
    last_status: str = "playing"
    interrupted: bool = False


class Tracker:
    def __init__(self, store: Store, complete_ratio: float = 0.95, discontinuity_seconds: float = 30.0) -> None:
        self.store = store
        self.complete_ratio = complete_ratio
        self.discontinuity = discontinuity_seconds
        self.current: Track | None = None
        self.last_tick = time.time()
        self.started_ts = time.time()
        self.status = "none"
        self.session_present = False
        self.unknown_duration_skipped = 0
        self.last_event_at: float | None = None
        self._on_event = None

    def on_event(self, callback) -> None:
        self._on_event = callback

    # ---------- 时长 ----------
    def _resolve_duration(self, track: Track) -> None:
        try:
            duration, source = lookup_duration(track.title, track.artist, self.store, track.key)
            track.duration = duration
            track.duration_source = source
            if duration:
                log.info("时长: %s - %s = %.0fs (%s)", track.title, track.artist, duration, source)
            else:
                log.info("时长未知: %s - %s (%s)", track.title, track.artist, source)
        except Exception:  # noqa: BLE001
            log.exception("查时长出错: %s", track.title)
            track.duration_source = "error"

    def _start(self, snap: Snapshot, now: float) -> None:
        track = Track(
            key=snap.key,
            title=snap.title,
            artist=snap.artist,
            album=snap.album,
            started_at=now,
            last_status=snap.status,
        )
        self.current = track
        self.status = snap.status
        log.info("开始: %s - %s (%s)", snap.title, snap.artist, snap.status)
        threading.Thread(target=self._resolve_duration, args=(track,), daemon=True).start()

    # ---------- 结束 ----------
    def _finish(self, track: Track, reason: str, now: float) -> None:
        duration = track.duration
        played = track.played

        if duration is None:
            if played >= UNKNOWN_DURATION_MIN_PLAY:
                self.unknown_duration_skipped += 1
            log.info("丢弃: %s - %s 时长未知（%s），不记录", track.title, track.artist, track.duration_source)
            return

        complete = played >= duration * self.complete_ratio and reason in (
            "switched",
            "stopped",
            "session_lost",
            "looped",
            "shutdown",
        )
        if not complete:
            log.info("丢弃: %s - %s 未听完 %.0fs / %.0fs", track.title, track.artist, played, duration)
            return

        event = {
            "id": str(uuid.uuid4()),
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
            "track_key": track.key,
            "started_at": iso(track.started_at),
            "ended_at": iso(now),
            "played_seconds": round(min(played, duration), 1),
            "duration_seconds": round(duration, 1),
            "completed": True,
            "end_reason": reason,
            "collector_version": VERSION,
        }
        if self._on_event:
            self._on_event(event)
        self.last_event_at = now
        log.info("记录: %s - %s 听完 %.0fs / %.0fs (%s)", track.title, track.artist, played, duration, reason)

    # ---------- 主循环每次调用 ----------
    def tick(self, snap: Snapshot, now: float) -> None:
        dt = now - self.last_tick
        self.last_tick = now
        if dt < 0:
            dt = 0

        interrupted = dt > self.discontinuity
        if interrupted and self.current:
            log.warning("采集间隔 %.0f 秒，视为中断，这段时间不计入听歌", dt)
            self.current.interrupted = True
        if interrupted:
            dt = 0

        prev = self.current
        self.session_present = snap.present
        same_track = bool(prev and snap.present and snap.key == prev.key)

        if same_track:
            assert prev is not None
            prev.last_status = snap.status
            self.status = snap.status
            if snap.status == "playing":
                prev.played += dt
                if prev.duration and prev.played > prev.duration + 5:
                    # 单曲循环：上一遍已完整，另起一条
                    self._finish(prev, "looped", now)
                    self.current = None
                    self._start(snap, now)
            elif snap.status in ("stopped", "closed"):
                self._finish(prev, "stopped", now)
                self.current = None
            return

        # 换歌 / 会话消失
        if prev:
            if prev.last_status == "playing":
                prev.played += dt
            self._finish(prev, "switched" if snap.present else "session_lost", now)
            self.current = None

        self.status = snap.status if snap.present else "none"
        if snap.present and snap.title:
            self._start(snap, now)

    # ---------- 对外状态 ----------
    def state_payload(self, extra: dict | None = None) -> dict:
        track = self.current
        payload = {
            "captured_at": iso(time.time()),
            "media": {
                "present": self.session_present,
                "status": self.status if track else ("none" if not self.session_present else self.status),
                "title": track.title if track else None,
                "artist": track.artist if track else None,
                "album": track.album if track else None,
                "position_seconds": round(track.played, 1) if track else None,
                "duration_seconds": round(track.duration, 1) if track and track.duration else None,
                "position_estimated": True,
                "duration_source": track.duration_source if track else None,
                "track_started_at": iso(track.started_at) if track else None,
                "interrupted": bool(track.interrupted) if track else False,
            },
            "collector": {
                "version": VERSION,
                "started_at": iso(self.started_ts),
                "uptime_seconds": round(time.time() - self.started_ts),
                "unknown_duration_skipped": self.unknown_duration_skipped,
                "last_event_at": iso(self.last_event_at) if self.last_event_at else None,
            },
        }
        if extra:
            payload["collector"].update(extra)
        return payload

    def finish_all(self, now: float | None = None) -> None:
        if self.current:
            self._finish(self.current, "shutdown", now or time.time())
            self.current = None
