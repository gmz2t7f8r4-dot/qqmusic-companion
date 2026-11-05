"""验证脚本：录 30 秒正在播放的声音，做一次浅层分析，打印人话报告。

用法：<venv python> listen_preview.py [秒数]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
OUT = Path(__file__).resolve().parent.parent / "local-data" / "preview.wav"


def record(seconds: int) -> np.ndarray:
    import soundcard as sc

    speaker = sc.default_speaker()
    mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
    print(f"录音设备: {speaker.name} / 回环 {mic.name}，{seconds} 秒…", flush=True)
    with mic.recorder(samplerate=48000, channels=2) as rec:
        data = rec.record(numframes=48000 * seconds)
    print("录完，开始分析…", flush=True)
    return data


def band_ratio(spec: np.ndarray, freqs: np.ndarray) -> dict:
    total = spec.sum() + 1e-9
    edges = [(20, 120), (120, 500), (500, 2000), (2000, 6000), (6000, 16000)]
    names = ["低频", "中低", "中频", "中高", "高频"]
    return {n: float(spec[(freqs >= lo) & (freqs < hi)].sum() / total) for n, (lo, hi) in zip(names, edges)}


def main() -> int:
    import librosa

    audio = record(SECONDS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sf.write(OUT, audio, 48000)

    mono = np.mean(audio, axis=1).astype(np.float32)
    sr = 48000
    duration = len(mono) / sr

    # 节奏
    tempo, beats = librosa.beat.beat_track(y=mono, sr=sr)
    tempo = float(np.atleast_1d(tempo)[0])

    # 能量曲线（每 2 秒一格）
    hop = 512
    rms = librosa.feature.rms(y=mono, hop_length=hop)[0]
    seg = max(1, int(len(rms) / max(1, duration / 2)))
    chunks = [float(rms[i:i + seg].mean()) for i in range(0, len(rms), seg)]
    chunks = [c for c in chunks if c > 0]

    # 音色
    cent = librosa.feature.spectral_centroid(y=mono, sr=sr)[0].mean()
    rolloff = librosa.feature.spectral_rolloff(y=mono, sr=sr, roll_percent=0.85)[0].mean()
    flux = librosa.onset.onset_strength(y=mono, sr=sr)
    onsets = librosa.onset.onset_detect(onset_envelope=flux, sr=sr)
    density = len(onsets) / duration

    spec = np.abs(np.fft.rfft(mono[: sr * 10])) if len(mono) > sr * 10 else np.abs(np.fft.rfft(mono))
    freqs = np.fft.rfftfreq(len(mono[: sr * 10]) if len(mono) > sr * 10 else len(mono), 1 / sr)
    bands = band_ratio(spec, freqs)

    # 能量走向
    if len(chunks) >= 3:
        head, mid, tail = chunks[0], chunks[len(chunks) // 2], chunks[-1]
        if tail > head * 1.35:
            trend = "整体在往上走"
        elif tail < head * 0.7:
            trend = "整体在往下退"
        else:
            trend = "能量比较平稳"
    else:
        head = mid = tail = chunks[0] if chunks else 0
        trend = "样本太短，看不出走向"

    low = bands["低频"] + bands["中低"]
    high = bands["高频"] + bands["中高"]

    lines = []
    lines.append(f"这是一段 {duration:.0f} 秒的声音。")
    lines.append(f"速度大约 {tempo:.0f} BPM，" + ("偏慢，像是留了呼吸的空间。" if tempo < 85 else "偏快，往前走的感觉。" if tempo > 120 else "中速，不急不缓。"))
    lines.append(f"音色亮度 {cent:.0f} Hz、" + ("偏暗，厚一些。" if cent < 1800 else "偏亮，细节多。" if cent > 3000 else "中等，不算刺耳。"))
    lines.append(f"低频占 {low * 100:.0f}%，高频占 {high * 100:.0f}%，" + ("重心压在下面，鼓和贝斯撑得住。" if low > 0.45 else "重心偏上，中高频更突出。" if high > 0.35 else "两头比较均衡。"))
    lines.append(f"起音密度每秒约 {density:.1f} 次，" + ("编曲很密，东西一直在动。" if density > 4 else "比较疏，留白多。" if density < 1.2 else "不密不疏。"))
    lines.append(f"这 30 秒里能量{trend}。")
    lines.append("")
    lines.append("（这是我自己量的粗浅结果，不是 Tinggu 的完整报告——完整版还会分乐器、量嗓音、看和声走向。）")

    print("\n".join(lines))
    print(f"\n录音文件: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
