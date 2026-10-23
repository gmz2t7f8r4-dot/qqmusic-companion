"""排查用：把 Windows 媒体会话里能读到的字段全部打印出来。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "local" / "vendor"))

from winrt.windows.media.control import (  # noqa: E402
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)


def main() -> int:
    manager = SessionManager.request_async().get()
    sessions = list(manager.get_sessions())
    print(f"会话数量: {len(sessions)}")
    for idx, session in enumerate(sessions):
        print(f"\n--- [{idx}] ---")
        print("app    :", session.source_app_user_model_id)
        try:
            info = session.try_get_media_properties_async().get()
            print("title  :", repr(info.title))
            print("artist :", repr(info.artist))
            print("album  :", repr(info.album_title))
            print("track# :", info.track_number)
            print("genres :", list(info.genres))
        except Exception as exc:  # noqa: BLE001
            print("media properties 读取失败:", exc)

        pb = session.get_playback_info()
        print("status :", PlaybackStatus(pb.playback_status).name, f"({pb.playback_status})")
        print("rate   :", pb.playback_rate, "shuffle:", pb.is_shuffle_active, "repeat:", pb.auto_repeat_mode)

        tl = session.get_timeline_properties()
        print("pos    :", tl.position)
        print("start  :", tl.start_time)
        print("end    :", tl.end_time)
        print("minseek:", tl.min_seek_time, "maxseek:", tl.max_seek_time)
        print("lastupd:", tl.last_updated_time)

        props = session.get_session_properties() if hasattr(session, "get_session_properties") else None
        if props:
            print("source :", props.source_app_user_model_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
