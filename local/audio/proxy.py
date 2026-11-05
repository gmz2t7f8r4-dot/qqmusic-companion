"""录音进程的"遥控器"：分析进程通过它读缓冲、拼 wav，自己不碰声音设备。"""

from __future__ import annotations

import json
import wave
from pathlib import Path


class RecorderProxy:
    def __init__(self, tmp_dir: Path) -> None:
        self.tmp_dir = Path(tmp_dir)
        self.buf_dir = self.tmp_dir / "buf"
        self.state_file = self.tmp_dir / "state.json"
        self.status_file = self.tmp_dir / "recorder-status.json"

    def _status(self) -> dict:
        try:
            return json.loads(self.status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def buffered_seconds(self) -> float:
        return float(self._status().get("buffered_seconds") or 0.0)

    @property
    def error(self) -> str | None:
        return self._status().get("error")

    def track_info(self) -> dict:
        try:
            st = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {
            "key": st.get("key", ""),
            "title": st.get("title", ""),
            "artist": st.get("artist", ""),
            "album": st.get("album", ""),
            "duration": st.get("duration"),
        }

    def snapshot(self, target: Path) -> tuple[str, str, float]:
        """把录音进程写出来的块按顺序拼成 wav。"""
        chunks = sorted(self.buf_dir.glob("*.pcm"))
        info = self.track_info()
        if not chunks:
            return info.get("key", ""), info.get("title", ""), 0.0

        frames = bytearray()
        for chunk in chunks:
            try:
                frames += chunk.read_bytes()
            except OSError:
                continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            wf.writeframes(bytes(frames))

        seconds = len(frames) / (48000 * 2)
        return info.get("key", ""), info.get("title", ""), seconds

    # 兼容原录音线程的接口（分析进程这边不需要真做事）
    def set_track(self, *args, **kwargs) -> None:
        return None

    def clear(self) -> None:
        return None

    def stop(self) -> None:
        return None
