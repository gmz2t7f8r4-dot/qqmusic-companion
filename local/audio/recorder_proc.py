"""录音专用进程：只干一件事——把系统正在放的声音按 0.5 秒一块写成文件。

为什么要单独一个进程：声音驱动（soundcard/cffi）在录音时几乎一直占着 Python 的全局锁，
放在分析进程里会把领任务、按任务分析的线程饿死（实测就是这个原因）。

它读 audio-tmp/state.json 知道当前在放哪首，切歌就把旧的块全删掉。
"""

from __future__ import annotations

import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR.parent / "local-data"
TMP_DIR = DATA_DIR / "audio-tmp"
BUF_DIR = TMP_DIR / "buf"
LAST_DIR = TMP_DIR / "last"          # 上一首的完整录音（切歌时从 buf 挪过来）
LAST_META = TMP_DIR / "last-meta.json"
STATE_FILE = TMP_DIR / "state.json"
STATUS_FILE = TMP_DIR / "recorder-status.json"

SAMPLE_RATE = 48000
BLOCK_SECONDS = 0.5
MAX_SECONDS = 600


def clear_buffer() -> None:
    BUF_DIR.mkdir(parents=True, exist_ok=True)
    for item in BUF_DIR.glob("*.pcm"):
        try:
            item.unlink()
        except OSError:
            pass


def rotate_to_last(meta: dict, chunks: int) -> bool:
    """切歌时：把当前这首的录音挪到 last/，只保留最近一首。

    太短的（< 15 秒）不覆盖：QQ 音乐切歌瞬间偶尔会来回报两次，
    不然会把刚放完那首的完整录音盖成新歌的头 1~2 秒。
    """
    seconds = chunks * BLOCK_SECONDS
    if seconds < 15:
        return False
    LAST_DIR.mkdir(parents=True, exist_ok=True)
    marker = LAST_DIR / ".rotating"
    marker.write_text("1", encoding="ascii")
    try:
        for old in LAST_DIR.glob("*.pcm"):
            try:
                old.unlink()
            except OSError:
                pass
        for item in sorted(BUF_DIR.glob("*.pcm")):
            try:
                item.replace(LAST_DIR / item.name)
            except OSError:
                pass
        meta = dict(meta)
        meta["seconds"] = round(seconds, 1)
        LAST_META.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    finally:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass
    return True


def main() -> int:
    import ctypes

    import soundcard as sc

    # 只允许一个录音进程：两个同时写同一份缓冲会互相覆盖，
    # 出现"录了 442 秒，切到 last 只剩 2 秒"这种怪事。
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, "Global\\QQMusicCompanionRecorder")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return 2
    _ = mutex  # 保持引用，进程退出才释放

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    BUF_DIR.mkdir(parents=True, exist_ok=True)
    clear_buffer()

    speaker = sc.default_speaker()
    mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
    block = int(SAMPLE_RATE * BLOCK_SECONDS)
    max_chunks = int(MAX_SECONDS / BLOCK_SECONDS)

    seq = 0
    current_key = ""
    current_meta: dict = {}
    last_state_read = 0.0
    error = None

    try:
        with mic.recorder(samplerate=SAMPLE_RATE, channels=2) as rec:
            while True:
                now = time.time()
                if now - last_state_read > 1:
                    last_state_read = now
                    try:
                        st = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
                        key = st.get("key") or ""
                        if key and key != current_key:
                            if current_key and seq > 0:
                                rotate_to_last(current_meta, seq)
                            clear_buffer()
                            current_key = key
                            current_meta = {
                                "key": key,
                                "title": st.get("title", ""),
                                "artist": st.get("artist", ""),
                                "album": st.get("album", ""),
                                "duration": st.get("duration"),
                                "rotated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            }
                            seq = 0
                    except (OSError, ValueError):
                        pass

                data = rec.record(numframes=block)
                if data is None or len(data) == 0:
                    continue
                if not current_key:
                    continue  # 还没开始放歌，不存

                mono = np.mean(data, axis=1)
                pcm = np.clip(mono, -1.0, 1.0)
                pcm16 = (pcm * 32767).astype(np.int16)
                (BUF_DIR / f"{seq:08d}.pcm").write_bytes(pcm16.tobytes())
                seq += 1

                if seq > max_chunks:
                    oldest = seq - max_chunks
                    try:
                        (BUF_DIR / f"{oldest:08d}.pcm").unlink()
                    except OSError:
                        pass

                STATUS_FILE.write_text(json.dumps({
                    "key": current_key,
                    "buffered_seconds": round(min(seq, max_chunks) * BLOCK_SECONDS, 1),
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "error": error,
                }, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        STATUS_FILE.write_text(json.dumps({"key": current_key, "buffered_seconds": 0,
                                           "error": error}, ensure_ascii=False), encoding="utf-8")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
