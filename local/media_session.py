"""读取 Windows 媒体会话里 QQ 音乐的状态。

实测（QQ 音乐 20.43）：能拿到歌名/歌手/专辑/播放状态，但时间轴（进度、总时长）恒为 0，
所以本模块只负责“是谁、在不在放”，进度由 tracker 用累计播放时间估算。
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "vendor"))

APP_ID_HINTS = ("qqmusic",)

# 与 Windows 的 PlaybackStatus 枚举对应
STATUS_NAMES = {
    0: "closed",
    1: "opened",
    2: "changing",
    3: "stopped",
    4: "playing",
    5: "paused",
}


@dataclass
class Snapshot:
    present: bool
    app_id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    status: str = "none"  # playing / paused / stopped / closed / changing / opened / none
    read_at: float = 0.0
    error: str | None = None

    @property
    def key(self) -> str:
        return f"{(self.title or '').strip().lower()}|{(self.artist or '').strip().lower()}|{(self.album or '').strip().lower()}"


class MediaSessionReader:
    def __init__(self) -> None:
        self._manager = None
        self._last_error: str | None = None

    def _ensure_manager(self):
        if self._manager is not None:
            return self._manager
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as SessionManager,
        )

        self._manager = SessionManager.request_async().get()
        return self._manager

    def read(self) -> Snapshot:
        started = time.time()
        try:
            manager = self._ensure_manager()
            sessions = list(manager.get_sessions())
        except Exception as exc:  # noqa: BLE001
            # 管理器失效（例如系统媒体服务重启）时丢弃，下次重建
            self._manager = None
            self._last_error = f"读取媒体会话失败: {exc}"
            return Snapshot(present=False, read_at=time.time(), error=self._last_error)

        target = None
        for session in sessions:
            app_id = (session.source_app_user_model_id or "").lower()
            if any(hint in app_id for hint in APP_ID_HINTS):
                target = session
                break

        if target is None:
            self._last_error = None
            return Snapshot(present=False, status="none", read_at=started)

        try:
            info = target.try_get_media_properties_async().get()
            playback = target.get_playback_info()
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"读取歌曲信息失败: {exc}"
            return Snapshot(present=True, status="unknown", read_at=time.time(), error=self._last_error)

        status = STATUS_NAMES.get(int(playback.playback_status), "unknown")
        self._last_error = None
        return Snapshot(
            present=True,
            app_id=target.source_app_user_model_id or "",
            title=(info.title or "").strip(),
            artist=(info.artist or "").strip(),
            album=(info.album_title or "").strip(),
            status=status,
            read_at=started,
        )

    @property
    def last_error(self) -> str | None:
        return self._last_error
