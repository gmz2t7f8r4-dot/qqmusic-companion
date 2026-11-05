"""录音缓冲：抓系统正在播放的声音（WASAPI 回环），按"歌"为单位保存。

规则（和用户/AI定的）：
- 只保留当前这一首；切歌就清空重录
- 单首最多保留 max_seconds 秒，超了从最前面丢
- 只在有人要听的时候才落盘成 wav；落盘文件用完就删
- 停止时清空
"""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np


class LoopbackRecorder(threading.Thread):
    def __init__(
        self,
        out_dir: Path,
        sample_rate: int = 48000,
        max_seconds: int = 600,
        block_seconds: float = 0.5,
        device_name: str | None = None,
    ) -> None:
        super().__init__(name="audio-recorder", daemon=True)
        self.out_dir = Path(out_dir)
        self.sample_rate = sample_rate
        self.max_seconds = max_seconds
        self.block_seconds = block_seconds
        self.device_name = device_name

        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._track_key = ""
        self._track_title = ""
        self._track_artist = ""
        self._track_album = ""
        self._track_duration: float | None = None
        self._started_at = time.time()
        self._stopping = threading.Event()
        self._error: str | None = None
        self.last_capture_at: float = 0.0

    # ---------- 外部调用 ----------
    def set_track(self, key: str, title: str = "", artist: str = "", album: str = "",
                  duration: float | None = None) -> None:
        """采集器每次读到当前歌时调用；key 变了就清空缓冲。"""
        if not key:
            return
        with self._lock:
            if key != self._track_key:
                self._chunks.clear()
                self._frames = 0
                self._track_key = key
                self._track_title = title
                self._track_artist = artist
                self._track_album = album
                self._track_duration = duration
                self._started_at = time.time()

    def track_info(self) -> dict:
        with self._lock:
            return {
                "key": self._track_key,
                "title": self._track_title,
                "artist": self._track_artist,
                "album": self._track_album,
                "duration": self._track_duration,
            }

    def snapshot(self, target: Path) -> tuple[str, str, float]:
        """把当前缓冲写成 wav，返回 (歌标识, 标题, 秒数)。"""
        with self._lock:
            chunks = list(self._chunks)
            key, title = self._track_key, self._track_title
        if not chunks:
            return key, title, 0.0

        audio = np.concatenate(chunks)
        seconds = len(audio) / self.sample_rate
        target.parent.mkdir(parents=True, exist_ok=True)
        pcm = np.clip(audio, -1.0, 1.0)
        pcm16 = (pcm * 32767).astype(np.int16)
        with wave.open(str(target), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm16.tobytes())
        return key, title, seconds

    def buffered_seconds(self) -> float:
        with self._lock:
            return self._frames / self.sample_rate

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._frames = 0

    def stop(self) -> None:
        self._stopping.set()

    @property
    def error(self) -> str | None:
        return self._error

    # ---------- 线程 ----------
    def run(self) -> None:
        try:
            import soundcard as sc
        except Exception as exc:  # noqa: BLE001
            self._error = f"录音库不可用: {exc}"
            return

        try:
            speaker = sc.default_speaker()
            if self.device_name:
                speaker = sc.get_speaker(self.device_name)
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        except Exception as exc:  # noqa: BLE001
            self._error = f"找不到可录的声卡: {exc}"
            return

        block = int(self.sample_rate * self.block_seconds)
        try:
            with mic.recorder(samplerate=self.sample_rate, channels=2) as rec:
                while not self._stopping.is_set():
                    data = rec.record(numframes=block)
                    if data is None or len(data) == 0:
                        continue
                    mono = np.mean(data, axis=1).astype(np.float32)
                    with self._lock:
                        if not self._track_key:
                            continue  # 还没开始放歌，不存
                        self._chunks.append(mono)
                        self._frames += len(mono)
                        limit = self.max_seconds * self.sample_rate
                        while self._frames > limit and self._chunks:
                            dropped = self._chunks.pop(0)
                            self._frames -= len(dropped)
                        self.last_capture_at = time.time()
        except Exception as exc:  # noqa: BLE001
            self._error = f"录音中断: {exc}"
