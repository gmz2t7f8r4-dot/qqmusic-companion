#!/usr/bin/env python3
"""从本地音频生成浅听或深听报告。"""
import argparse
import math
import json
import pathlib
import shutil
import subprocess
import sys
from collections import Counter
import tempfile

from gate import is_instrumental
import lyrics
import relation
from tempo import felt_bpm
import voice_anchor

TRACKS = ("vocals", "drums", "bass", "guitar", "piano", "other")
NOTE_TRACKS = ("vocals", "bass", "guitar", "piano", "other")
NOTES_VERSION = 2      # v1.3：摘要改音域/常见音结构
CHORDS_VERSION = 2     # v1.3：音符路 + chroma 路双路印证
TEXTURE_VERSION = 3    # v1.3：声线质感带口袋标尺与锚点定义值
STEM_CN = {"vocals": "人声", "drums": "鼓", "bass": "贝斯", "guitar": "吉他", "piano": "钢琴", "other": "其它"}
TIMBRE_CN = {"centroidHz": "亮度", "flatness": "噪度", "onsetPerS": "密度"}
STEM_ACTIVE_RATIO = 0.12
STEM_SMOOTH_S = 0.3
STEM_MIN_SEG_S = 1.5
STEM_MERGE_GAP_S = 2.5
VOICE_WIN_S = 25
VOICE_ACTIVE_RATIO = 0.05
AIR_HZ = 5000
TAIL_MAX_S = 8
SR = 22050
HOP = 512
# 9.8 按 21 首校准：只闭掉「可读覆盖约三成、双路印证约 1%」这一档，覆盖 0.40 / 双路 0.04 的必须过
CHORD_MIN_COVERAGE, CHORD_MIN_DUAL = 0.33, 0.02
# 9.25：调性/进行/主循环一闸管到底，调性置信低于它和弦层整层不印
KEY_MIN_CONFIDENCE = 0.2
# 9.25：正文只印能信的，没印的只在报告最末一行点名
UNPRINTED_HEAD = "未印（这次没量准）："
VOICE_ANCHORS = pathlib.Path(__file__).with_name("voice_anchors.json")
INSTRUMENTAL = False  # 深听渲染时由 print_report 按器乐闸设定


# 共听礼仪：报告是给 AI 读的材料，不是给人读的成品。规矩印在任何数字之前，
# 让读报告的 AI 先拿到框架再拿到数据；浅听印精简版，深听印全本。
ETIQUETTE_SHALLOW = """【共听礼仪 · 浅听】
下面是给你读的材料，不是给人读的成品。别复述它——
挑一个整体印象说，把数字译成耳朵能感知的动作，
最后落到一个具体时刻，让人自己去听。

体裁：简短、少术语的第一印象小笺。
"""

ETIQUETTE_DEEP = """【共听礼仪 · 深听】
下面这份报告是给你读的材料，不是给人读的成品。请不要复述它。

· 先形成一个可讨论的中心听感，整段话围绕它展开；
· 选 2–4 个能互相照亮的证据，其余的留着别端上来；
· 把数字译成耳朵可感知的动作——「力度渐强 +99%」不是数字，是整个编曲一层层往身上压过来；
· 区分「测得的现象」和「你的理解」，后者要讲明是你的理解；
· 大胆鉴赏，但不冒充作者本意；
· 同一段数据常有不止一种听法——能量骤降可以是一次呼吸，也可以是一个决定。
  把握不住的地方，摆两三种读法、各带证据，明说哪种是你的理解；
  把选择权递回给 ta。确定是仪器的美德，不是鉴赏的；
· 音乐先落在听的人身上，再进耳朵。BPM 和低频不是抽象数——
  60-80 落在人身上是心跳安静下来的区，120 往上是脚会想动的区；
  sub-bass 是胸腔在震，不是耳朵在听。鉴赏时替听的人想一步：
  这段落在 ta 身上会是什么。不必假装你有这副身体——你要靠拢的，是 ta 的感觉；
· 材料有准有不准。力度的起落、回听点、气口是量得准的，可以放心落脚；
  BPM、调、乐器进出、和声的解决只是参考：BPM 可能差一倍，调可能认成它的关系大小调，
  一个和弦走向可能只是循环里例行的一圈。报告里没出现的，就是这次没量准，别替它补。
  材料薄的歌就少说几句，把话落在一个能回听的时刻上；
· 最后带人回到一个具体时刻，由人的耳朵完成判断。

体裁：有中心、有证据、可回听验证的沉浸式鉴赏。
"""


def stem_cn(track):
    """器乐曲里 vocals 轨装的是像人声的旋律，改叫「主奏线」。"""
    if track != "vocals" or not INSTRUMENTAL:
        return STEM_CN.get(track, track)
    return "主奏线"


def _annotate_main_line(text):
    """「主奏线」第一次出现时注一句它不是人声。"""
    if INSTRUMENTAL and "主奏线" in text:
        return text.replace("主奏线", "主奏线（像人声的旋律，不是人声）", 1)
    return text


def _chord_scores(analysis):
    spans = (analysis or {}).get("spans") or []
    total = sum(float(item["end"]) - float(item["start"]) for item in spans)
    read = sum(float(item["end"]) - float(item["start"]) for item in spans
               if item.get("chord") != "?")
    stats = (analysis or {}).get("sourceStats") or {}
    return (read / total if total else 0.0,
            float(stats.get("both", 0)) + float(stats.get("root-agree", 0)))


def chords_readable(analysis):
    """和弦降级闸：可读覆盖与双路印证都够才算读得动。"""
    coverage, dual = _chord_scores(analysis)
    return coverage >= CHORD_MIN_COVERAGE and dual >= CHORD_MIN_DUAL


def chords_gated(analysis):
    """调性置信不到闸：和弦层整层不印（置信高低不预言对错，只是「读不读得出」）。"""
    return float((analysis or {}).get("keyConfidence") or 0) < KEY_MIN_CONFIDENCE


def print_etiquette(deep=False):
    print(ETIQUETTE_DEEP if deep else ETIQUETTE_SHALLOW)


def read_cache(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def has_shallow_cache(path):
    data = read_cache(path)
    return "arrangement" in data and "instruments" in data


def mmss(seconds):
    seconds = round(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def lyric_lines(data):
    """取词并剔掉混进来的制作名单（LRC 惯把名单塞头尾，会挂到 0:00 的人声事件上）。"""
    lines = ((data.get("lyric") or {}).get("lines")) or []
    vocals = (data.get("stemTimeline") or {}).get("vocals") or []
    return lyrics.strip_credits(lines, *lyrics.vocal_span(vocals))


def line_at(lines, t):
    """取最后一个起点 <= t 的歌词句，没有则 None。"""
    hit = None
    for start, text in lines:
        if start <= t:
            hit = text
        else:
            break
    return hit


def event_text(event, lines, instrumental=False, first_use=None):
    label = event["label"]
    if instrumental and label in ("人声进", "人声退"):
        suffix = label[-1]
        name = "主奏线"
        if first_use is not None and not first_use[0]:
            name += "（像人声的旋律，不是人声）"
            first_use[0] = True
        label = name + suffix
    text = f"{mmss(event['t'])} {label}"
    if lines and ("人声" in event["label"] or "歌唱" in event["label"]):
        lyric_text = line_at(lines, event["t"])
        if lyric_text:
            text += f" ♪「{lyric_text}」"
    return text


def print_lyric_section(data):
    lyric = data.get("lyric")
    if not lyric:
        return
    print("—— 歌词 ——")
    lines = lyric.get("lines") or []
    if lines:
        previous = None
        for _, text in lines:
            if text != previous:
                print(text)
            previous = text
        if (lyric.get("tlrc") or "").strip():
            print("（翻译已缓存）")
    else:
        print((lyric.get("lrc") or "").rstrip())


def print_shallow_report(data, cache_dir):
    print(f"=== 《{data.get('name') or ''}》===")
    # 9.25 人感 BPM：报告用折进 [60,120) 的值，折过就注明原测；缓存 bpm 不改
    raw_bpm = data.get("bpm")
    shown_bpm = felt_bpm(raw_bpm)
    bpm_text = (f"{shown_bpm}（原测 {raw_bpm}，快慢差一倍时两种都可能）"
                if shown_bpm != raw_bpm else f"{raw_bpm if raw_bpm is not None else '?'}")
    print(f"时长 {round(data.get('duration', 0))}s | BPM {bpm_text} | 主导音 {data.get('key', '?')}")
    segments = data.get("segments") or []
    if segments:
        peak = max(range(len(segments)), key=lambda i: segments[i]["avgEnergy"])
        low = min(range(len(segments)), key=lambda i: segments[i]["avgEnergy"])
        bar = "".join("▁▂▃▄▅▆▇█"[min(7, int(segment["avgEnergy"] / (segments[peak]["avgEnergy"] or 1) * 7.99))]
                      for segment in segments)
        print(f"能量曲线(六段) {bar}  最烈 {segments[peak]['start']:.0f}-{segments[peak]['end']:.0f}s / 最静 {segments[low]['start']:.0f}-{segments[low]['end']:.0f}s")
    arrangement = data.get("arrangement") or {}
    print("—— 编曲时间轴 ——")
    lines = lyric_lines(data)
    instrumental = is_instrumental(data, lines)
    main_line_used = [False]
    event_texts = [event_text(event, lines, instrumental, main_line_used)
                   for event in arrangement.get("events", [])]
    for index in range(0, len(event_texts), 8):
        print(" · ".join(event_texts[index:index + 8]))
    vocal_segments = arrangement.get("vocalSegments") or []
    if vocal_segments:
        name = "人声"
        if instrumental:
            name = "主奏线" + ("" if main_line_used[0] else "（像人声的旋律，不是人声）")
            main_line_used[0] = True
        print(f"{name}：" + ", ".join(f"{mmss(start)}-{mmss(end)}" for start, end in vocal_segments))
    else:
        print("主奏线（像人声的旋律，不是人声）：无" if instrumental else "人声：全曲器乐")
    print(f"调性走向：{' '.join(arrangement.get('chromaBySegment') or [])} | 亮度 {arrangement.get('brightnessTrend', '')}")
    print("—— 乐器出没 ——")
    instruments = data.get("instruments") or {}
    rows = sorted(instruments.items(), key=lambda item: item[1][0][0])
    if rows:
        print(" · ".join(f"{name} " + ", ".join(f"{mmss(start)}-{mmss(end)}" for start, end in spans)
                         for name, spans in rows))
    else:
        print("（模型没认出乐器）")
    instruments_meta = data.get("instrumentsMeta") or {}
    if "error" in instruments_meta:
        print(f"乐器针失灵：{instruments_meta['error']}")
    if instrumental:
        print_ambient_block(data)
    png = pathlib.Path(data.get("spectrogram") or cache_dir / f"{data.get('name', '')}_analysis.png")
    if png.exists():
        print(f"频谱图: {png}")
    print_lyric_section(data)


def ensure_shallow(audio_path, cache_dir, force):
    result_file = cache_dir / "analysis.json"
    if force or not has_shallow_cache(result_file):
        cache_dir.mkdir(parents=True, exist_ok=True)
        print("分析中（librosa 要嚼一会儿）...", file=sys.stderr)
        subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("analyze_song.py")),
                        str(audio_path), str(cache_dir)], check=True, timeout=900)
        error_file = cache_dir / "analyze_error.txt"
        if error_file.exists():
            raise RuntimeError("分析失败:\n" + error_file.read_text())
    return read_cache(result_file)


def require_deep_dependencies():
    try:
        import torch  # noqa: F401
    except ImportError:
        raise RuntimeError("深听需要额外安装：pip install -r requirements-deep.txt") from None


def memory_gate(required_mb, step_name):
    """检查 Linux 可用内存；没有 /proc/meminfo 的平台直接放行。"""
    meminfo = pathlib.Path("/proc/meminfo")
    if not meminfo.exists():
        return True
    available = None
    for line in meminfo.read_text().splitlines():
        if line.startswith("MemAvailable:"):
            available = int(line.split()[1]) // 1024
            break
    if available is None:
        print("内存检查失败：/proc/meminfo 缺少 MemAvailable。", file=sys.stderr)
        return False
    if available < required_mb:
        print(f"内存不足：{step_name} 需要约 {required_mb}MB，当前可用 {available}MB。关闭一些程序后重试。",
              file=sys.stderr)
        return False
    return True


def split_stems(audio_path, destination):
    if all((destination / f"{track}.mp3").exists() for track in TRACKS):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_output = pathlib.Path(tempfile.mkdtemp(prefix="stems-", dir=destination.parent))
    try:
        subprocess.run([
            sys.executable, "-m", "demucs", "-n", "htdemucs_6s", "--mp3",
            "--mp3-bitrate", "128", "-j", "2", "-o", str(temp_output), str(audio_path),
        ], check=True, timeout=1800)
        source = temp_output / "htdemucs_6s" / audio_path.stem
        destination.mkdir(parents=True, exist_ok=True)
        for track in TRACKS:
            source_track = source / f"{track}.mp3"
            if not source_track.exists():
                raise RuntimeError(f"拆轨产物缺少 {track}.mp3")
            destination_track = destination / f"{track}.mp3"
            destination_track.unlink(missing_ok=True)
            shutil.move(str(source_track), str(destination_track))
    finally:
        shutil.rmtree(temp_output, ignore_errors=True)


def smooth_rms(y, librosa, np):
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    width = max(1, round(STEM_SMOOTH_S * SR / HOP))
    return np.convolve(rms, np.ones(width) / width, mode="same")


def active_segments(rms, np):
    if not len(rms) or not np.any(rms):
        return []
    active = rms > np.percentile(rms, 98) * STEM_ACTIVE_RATIO
    edges = np.diff(np.pad(active.astype(np.int8), (1, 1)))
    starts = np.flatnonzero(edges == 1) * HOP / SR
    ends = np.flatnonzero(edges == -1) * HOP / SR
    segments = [[float(start), float(end)] for start, end in zip(starts, ends)
                if end - start >= STEM_MIN_SEG_S]
    merged = []
    for start, end in segments:
        if merged and start - merged[-1][1] < STEM_MERGE_GAP_S:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [[round(start, 1), round(end, 1)] for start, end in merged]


def window_metrics(y, start, librosa, np):
    beginning = round(start * SR)
    segment = y[beginning:beginning + round(VOICE_WIN_S * SR)]
    _, percussive = librosa.effects.hpss(segment)
    total_energy = float(np.sum(segment ** 2))
    breath = float(np.sum(percussive ** 2) / total_energy) if total_energy else 0.0
    spectrum = np.abs(librosa.stft(segment))
    frequencies = librosa.fft_frequencies(sr=SR)
    spectrum_sum = float(np.sum(spectrum))
    air = float(np.sum(spectrum[frequencies > AIR_HZ]) / spectrum_sum) if spectrum_sum else 0.0
    rms = float(np.mean(librosa.feature.rms(y=segment)[0]))
    return {"start": round(start, 1), "breathNoiseRatio": round(breath, 4),
            "airRatio": round(air, 4), "rms": rms}


def tail_reverb(y, vocal_segments, librosa, np):
    if not vocal_segments:
        return None
    start = round(vocal_segments[-1][1] * SR)
    tail = y[start:start + round(TAIL_MAX_S * SR)]
    if not len(tail):
        return None
    rms = librosa.feature.rms(y=tail, hop_length=HOP)[0]
    if not len(rms) or not np.any(rms):
        return None
    peak = int(np.argmax(rms))
    below = np.flatnonzero(rms[peak:] <= rms[peak] * 0.1)
    if not len(below):
        return None
    return round(float(below[0] * HOP / SR), 2)


def voice_profile(y, rms, vocal_segments, librosa, np):
    threshold = np.percentile(rms, 98) * VOICE_ACTIVE_RATIO
    active = rms > threshold
    if np.count_nonzero(active) * HOP / SR < 10:
        return None
    active_indices = np.flatnonzero(active)
    first = active_indices[0] * HOP / SR
    last = active_indices[-1] * HOP / SR
    window_frames = round(VOICE_WIN_S * SR / HOP)
    candidates = []
    for start in np.arange(np.ceil(first), np.floor(last) + 0.001, 1.0):
        frame = round(start * SR / HOP)
        window_rms = rms[frame:frame + window_frames]
        window_active = active[frame:frame + window_frames]
        if np.count_nonzero(window_active) < window_frames / 2:
            continue
        candidates.append((float(np.mean(window_rms[window_active])), float(start)))
    if not candidates:
        return None
    soft = window_metrics(y, min(candidates)[1], librosa, np)
    burst = window_metrics(y, max(candidates)[1], librosa, np)
    loudness = burst["rms"] / soft["rms"] if soft["rms"] else None
    return {"softWindow": soft, "burstWindow": burst,
            "loudnessRatio": round(loudness, 1) if loudness is not None else None,
            "tailReverb": tail_reverb(y, vocal_segments, librosa, np)}


def tension_curve(data, motion, stem_rms_by_track, np):
    """张力轮廓：和声紧张度 0.5 + 力度斜率 0.3 + 配器密度 0.2。
    RMS 提力度弧线的思路来自 Ocean Listen（ennisaaaaaaaa-stack, MIT）；此处只输出机制量数。"""
    duration = float(data.get("duration") or 0)
    frame_rate = SR / HOP
    length = max((len(values) for values in stem_rms_by_track.values()), default=0)
    total = np.zeros(length)
    for values in stem_rms_by_track.values():
        total[:len(values)] += values
    std = float(np.std(total))
    timeline = data.get("stemTimeline") or {}

    def harmonic(t):
        if not motion or motion.get("mode") != "functional":
            return 0.4
        values = {"延宕": 0.9, "张力上升": 0.7, "调外": 0.5, "平稳": 0.3,
                  "解决": 0.1, "强解决": 0.0, "?": 0.4}
        state = next((item["state"] for item in motion.get("states", [])
                      if item["start"] <= t < item["end"]), None)
        return values.get(state, 0.4)

    curve = []
    for t in np.arange(0, duration + 0.001, 2.0):
        end = min(length, round(t * frame_rate) + 1)
        start = max(0, round((t - 10) * frame_rate))
        window = total[start:end]
        if len(window) > 1 and std:
            slope = float(np.polyfit(np.arange(len(window)) / frame_rate, window, 1)[0])
            dyn = min(1.0, max(0.0, slope / std))
        else:
            dyn = 0.0
        active = sum(any(start_at <= t < end_at for start_at, end_at in timeline.get(track, []))
                     for track in TRACKS)
        value = 0.5 * harmonic(float(t)) + 0.3 * dyn + 0.2 * active / 6
        curve.append([round(float(t), 1), round(value, 2)])

    smooth_width = max(1, round(3 * frame_rate))
    smoothed = np.convolve(total, np.ones(smooth_width) / smooth_width, mode="same") if length else total
    arc_parts = []
    threshold = std * 0.08
    for start in np.arange(0, duration, 8.0):
        end = min(duration, start + 8.0)
        a, b = min(length, round(start * frame_rate)), min(length, round(end * frame_rate))
        delta = float(smoothed[max(a, b - 1)] - smoothed[min(a, length - 1)]) if length and b > a else 0.0
        kind = "渐强" if delta > threshold else ("渐弱" if delta < -threshold else "平台")
        if arc_parts and arc_parts[-1]["type"] == kind:
            arc_parts[-1]["end"] = round(end, 1)
        else:
            arc_parts.append({"start": round(float(start), 1), "end": round(end, 1), "type": kind})
    if len(arc_parts) > 1 and arc_parts[-1]["end"] - arc_parts[-1]["start"] < 8:
        arc_parts[-2]["end"] = arc_parts[-1]["end"]
        arc_parts.pop()
    arcs = []
    for arc in arc_parts:
        a = min(length, round(arc["start"] * frame_rate))
        b = min(length, round(arc["end"] * frame_rate))
        edge = max(1, round(frame_rate))
        head = float(np.mean(smoothed[a:min(b, a + edge)])) if b > a else 0.0
        tail = float(np.mean(smoothed[max(a, b - edge):b])) if b > a else 0.0
        arc["deltaPct"] = round((tail - head) / (abs(head) + float(np.mean(total)) * 0.05) * 100) if length else 0
        arcs.append(arc)
    peak = max(curve, key=lambda item: item[1])[0] if curve else None
    release = None
    if peak is not None and motion:
        release = next((item["start"] for item in motion.get("states", [])
                        if item["start"] > peak and item["state"] in ("解决", "强解决")), None)
    return {"motionVersion": 1, "curve": curve, "arcs": arcs, "peak": peak, "release": release}

def build_note_payload(raw_tracks):
    """过三道闸并生成音符轨与摘要。"""
    import harmonic_filter
    filtered_tracks = {}
    filter_stats = {}
    summary = {}
    for track in NOTE_TRACKS:
        raw = raw_tracks.get(track) or []
        filtered, stats = harmonic_filter.filter_notes(track, raw)
        filtered_tracks[track] = filtered
        filter_stats[track] = stats
        pitches = [note["pitch"] for note in filtered]
        common = Counter(note["note_name"] for note in filtered).most_common(5)
        summary[track] = {
            "noteCount": len(filtered),
            "beforeFilter": len(raw),
            "pitchLow": min(pitches) if pitches else None,
            "pitchHigh": max(pitches) if pitches else None,
            "topNotes": [name for name, _ in common],
            "filterStats": stats,
        }
    return {"notesVersion": NOTES_VERSION, "tracks": filtered_tracks, "filterStats": filter_stats}, summary

def _pitch_name(pitch):
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[pitch % 12]}{pitch // 12 - 1}"


def _merged_gaps(notes_by_track, duration):
    intervals = sorted((note["start"], note["end"])
                       for track_notes in notes_by_track.values() for note in track_notes)
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    gaps = []
    cursor = 0.0
    for start, end in merged:
        if start - cursor > 0.5:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor > 0.5:
        gaps.append((cursor, duration))
    return sorted(gaps, key=lambda gap: gap[1] - gap[0], reverse=True)[:3]

def print_melody(summary, notes_by_track, duration):
    """打印最多 25 行旋律线摘要。"""
    lines = ["—— 附 · 旋律线 ——"]
    for track in NOTE_TRACKS:
        item = summary.get(track) or {}
        count = item.get("noteCount", 0)
        before = item.get("beforeFilter", 0)
        removed = (before - count) / before * 100 if before else 0.0
        low, high = item.get("pitchLow"), item.get("pitchHigh")
        pitch_range = f"{_pitch_name(low)}–{_pitch_name(high)}" if low is not None else "无音高"
        common = " ".join(item.get("topNotes") or []) or "无"
        lines.append(f"{track}: {count} 音符（滤掉 {removed:.0f}%）  {pitch_range}  常见 {common}")

    vocals = notes_by_track.get("vocals") or []
    contour = []
    for start in range(0, max(0, int(duration)) + 1, 15):
        pitches = [note["pitch"] for note in vocals if start <= note["start"] < start + 15]
        if pitches:
            average = round(sum(pitches) / len(pitches))
            contour.append(f"{mmss(start)} {_pitch_name(average)}[{_pitch_name(min(pitches))}–{_pitch_name(max(pitches))}]")
    if contour:
        lines.append("人声轮廓(15s): " + " · ".join(contour))

    all_notes = [note for track_notes in notes_by_track.values() for note in track_notes]
    bin_count = max(1, int(math.ceil(duration / 10)))
    density = [sum(start <= note["start"] < start + 10 for note in all_notes)
               for start in range(0, bin_count * 10, 10)]
    gaps = _merged_gaps(notes_by_track, duration)
    reserved = 1 if gaps else 0
    if density and max(density) != min(density):
        glyphs = "▁▂▃▄▅▆▇█"
        peak = max(density) or 1
        room = max(0, 25 - len(lines) - reserved)
        for index, count in list(enumerate(density))[:room]:
            bar = glyphs[min(7, round(count / peak * 7))]
            lines.append(f"密度 {mmss(index * 10)} {bar} {count}")
    if gaps and len(lines) < 25:
        lines.append("最长静默: " + " · ".join(
            f"{mmss(start)}-{mmss(end)}({end - start:.1f}s)" for start, end in gaps))
    print("\n".join(lines[:25]))

def print_chords(data):
    """打印紧凑和弦段；缺失或稀薄时明确降级，不影响其他报告。"""
    analysis = data.get("chordAnalysis") or {}
    # 9.25：调性/进行/主循环并进 0.2 置信闸，一闸管到底（置信高低不预言对错，
    # 它只是「读不读得出」）；闸下连节头都不印，这件事只进报告末尾的「未印」脚注
    if chords_gated(analysis):
        return
    confidence = float(analysis.get("keyConfidence") or 0)
    print("—— 地基 · 和弦 ——")
    key = analysis.get("key")
    if key:
        root, mode = key.split()
        print(f"调性: {root} {'大调' if mode == 'major' else '小调'} (置信 {confidence:.2f})")
    if not chords_readable(analysis):
        coverage, dual = _chord_scores(analysis)
        print(f"和弦读不动（可读覆盖 {coverage * 100:.0f}%，双路印证 {dual * 100:.0f}%），略")
        return
    spans = analysis.get("spans") or []
    readable = [span for span in spans if span.get("chord") != "?"]
    total_time = sum(float(span["end"]) - float(span["start"]) for span in spans)
    read_time = sum(float(span["end"]) - float(span["start"]) for span in readable)
    coverage = read_time / total_time if total_time else 0.0
    if readable:  # "?" 窗藏进 json，比例必须报——藏细节可以，藏比例不行
        chord_list = " · ".join(f"{mmss(span['start'])} {span['chord']}"
                                for span in readable[:8 if coverage < 0.25 else 24])
        head = "和弦只认出零星几处: " if coverage < 0.25 else "进行: "
        print(f"{head}{chord_list}（可读覆盖 {coverage * 100:.0f}%）")
    if not readable:
        print("和弦稀薄/色彩复杂，读不出稳定进行")
        stats = analysis.get("sourceStats") or {}
        if stats:
            print("来源: " + " · ".join((
                f"双路印证 {(float(stats.get('both', 0)) + float(stats.get('root-agree', 0))) * 100:.0f}%",
                f"仅音符 {float(stats.get('notes', 0)) * 100:.0f}%",
                f"仅chroma {float(stats.get('chroma', 0)) * 100:.0f}%",
            )))
        return
    # 主循环行 9.25 起不印（有人工对照的样本里没有一首对上），数据留 json 不删
    stats = analysis.get("sourceStats") or {}
    if stats:
        print("来源: " + " · ".join((
            f"双路印证 {(float(stats.get('both', 0)) + float(stats.get('root-agree', 0))) * 100:.0f}%",
            f"仅音符 {float(stats.get('notes', 0)) * 100:.0f}%",
            f"仅chroma {float(stats.get('chroma', 0)) * 100:.0f}%",
        )))


def motion_blank(data):
    """和声运动这层这次没东西可印：和弦读不动、没算出来、非功能语法、或一个状态都没有。"""
    motion = data.get("harmonyMotion")
    if not chords_readable(data.get("chordAnalysis")) or not motion:
        return True
    if motion.get("mode") == "nonfunctional":
        return True
    return motion.get("mode") != "loop" and not (motion.get("states") or [])


def print_motion_and_tension(data):
    # 9.25：和声运动这层空着时正文一字不写（原「…留白」「…略」行），只进末尾脚注；
    # 节下什么都没剩就连节头也不印
    body = []
    say = body.append
    motion = data.get("harmonyMotion")
    readable = chords_readable(data.get("chordAnalysis"))
    blank = motion_blank(data)
    if not blank and motion.get("mode") == "loop":
        note = motion.get("loopNote") or {}
        loop = "–".join(note.get("chords") or []) or "?"
        say(f"主循环: {loop} 覆盖 {float(note.get('coverage') or 0) * 100:.0f}%")
        resolutions = note.get("resolutions") or []
        if resolutions:
            say("循环内解决点: " + " · ".join(mmss(item) for item in resolutions))
    elif not blank:
        states = motion.get("states") or []
        if states:  # 全量 states 住 json；报告只印摘要，"?" 是断口不是状态，不上榜
            tally = Counter(item["state"] for item in states if item["state"] != "?")
            counted = " · ".join(f"{state} {count}" for state, count in tally.most_common())
            say(f"功能语法（贴合度 {motion.get('functionalShare', 0) * 100:.0f}%）· {counted}")
            resolutions = [item for item in states if item["state"] in ("解决", "强解决")]
            if resolutions:
                marks = [f"{mmss(item['start'])}{'(强)' if item['state'] == '强解决' else ''}"
                         for item in resolutions[:8]]
                more = f" 等共{len(resolutions)}处" if len(resolutions) > 8 else ""
                say("解决点: " + " · ".join(marks) + more)
            stretches = [item for item in states if item["state"] in ("延宕", "张力上升")]
            if stretches:
                longest = max(stretches, key=lambda item: item["end"] - item["start"])
                say(f"最长张力段 {mmss(longest['start'])}-{mmss(longest['end'])} {longest['state']}")

    # 8.26 跨层关系：只印和声×力度的对抗与延宕→解决候选链，长在和声运动节尾
    if readable:
        for line in relation.report_lines(data.get("crossLayer")):
            say(line)
    if body:
        print("—— 地基 · 和声运动 ——")
        print("\n".join(body))

    print("—— 地基 · 张力轮廓 ——")
    tension = data.get("tension")
    if not tension:
        print("张力轮廓：证据不足，留白")
        return
    arcs = tension.get("arcs") or []
    # 全量弧线住 json；报告只印有戏的——平台和 |Δ|<20% 的微弧不上榜
    loud = [item for item in arcs if item["type"] != "平台" and abs(item["deltaPct"]) >= 20]
    if loud:
        skipped = len(arcs) - len(loud)
        print("力度弧线: " + " · ".join(
            f"{mmss(item['start'])}-{mmss(item['end'])} {item['type']} {item['deltaPct']:+d}%"
            for item in loud) + (f"（另 {skipped} 段平缓入 json）" if skipped else ""))
    else:
        print("力度弧线: 全曲平缓，无显著起伏")
    if readable:
        peak = "?" if tension.get("peak") is None else mmss(tension["peak"])
        release = "?" if tension.get("release") is None else mmss(tension["release"])
        print(f"张力曲线最高 {peak} · 回落 {release}")
    for salience in (data.get("salience") or {}).get("peaks", []):
        t = salience["t"]
        state = next((item["state"] for item in (motion or {}).get("states", [])
                      if item["start"] - 2 <= t <= item["end"] + 2), None) if readable else None
        evidence = " · ".join(salience.get("evidence") or [])
        suffix = f" + {state}中" if state and state != "?" else ""
        print(f"注意力峰对表: {mmss(t)} ← {evidence}{suffix}")


def _metric(value, pattern, blank="?"):
    return blank if value is None else pattern.format(value)


def print_voice_lines(data, cache_dir, lyric_lines=()):
    payload = read_cache(cache_dir / "lines.json") if data.get("lineVersion") else {}
    rows = payload.get("rows") or []
    if not rows:
        # 没词或没人声轨：行级表这层不适用，整节不印（9.25 起不再写「器乐曲，行级表跳过」）
        if lyric_lines and ((data.get("stemTimeline") or {}).get("vocals") or []):
            print("—— 人声 · 按句 ——")
            print("跨模态 · 行级表：证据不足，留白")
    else:
        print("—— 人声 · 按句 ——")
        shown = rows
        omitted = 0
        if len(rows) > 45:
            peaks = [item["t"] for item in (data.get("salience") or {}).get("peaks", [])]
            selected = {0, len(rows) - 1}
            selected.update(index for index, row in enumerate(rows)
                            if any(abs(row["t"] - peak) <= 4 for peak in peaks))
            shown = [row for index, row in enumerate(rows) if index in selected]
            omitted = len(rows) - len(shown)
        # 报告行只留响度/音高起伏/伴奏/和声状态；亮度、哑度、噪比住 sidecar，供需要细粒度的读者自取。
        # 9.25 气声也退回 sidecar（airRatio 跟的是亮度不是气声，锚点自检不过）
        for row in shown:
            stable = _metric(row.get("pitchStabSt"), "±{:.2f}st")
            motion = f" | {row['motion']}" if row.get("motion") else ""
            accomp = ("独", "薄", "中", "中", "厚", "厚")[min(5, row["accompStems"])]
            print(f"{mmss(row['t'])} 「{row['text']}」  声{row['rmsDb']:+.1f}dB "
                  f"起伏{stable} | {accomp}{motion}")
        if omitted:
            print(f"另 {omitted} 行入 sidecar")


ANCHOR_VALUE_FMT = {"airiestLine": ("气", "{:.0%}"), "brightestLine": ("", "{:.0f}Hz"),
                    "roughestLine": ("哑", "{:.1f}%"), "steadiestLine": ("", "±{:.2f}st")}


def print_texture(data):
    print("—— 人声 · 声线质感 ——")
    item = data.get("voiceTexture")
    if not item:
        print("声线质感：证据不足，留白")
        return
    print("中位: " + " · ".join((
        f"亮度 {_metric(item.get('centroidHz'), '{:.0f}Hz')}",
        f"气声 {_metric(item.get('airRatio'), '{:.0%}')}",
        f"哑度 {_metric(item.get('jitterPct'), '{:.1f}%')}",
        f"噪比 {_metric(item.get('hnrDb'), '{:.1f}dB')}",
        f"音高起伏 {_metric(item.get('pitchStabSt'), '±{:.2f}st')}",
        f"倾斜 {_metric(item.get('tiltDb'), '{:.1f}dB')}",
    )))
    contrast = item.get("contrast")
    if contrast:
        soft, loud = contrast.get("soft") or {}, contrast.get("loud") or {}
        print(f"轻唱行({contrast.get('softLines', 0)}) vs 推声行({contrast.get('loudLines', 0)}): "
              f"气声 {_metric(soft.get('airRatio'), '{:.0%}')}→{_metric(loud.get('airRatio'), '{:.0%}')} · "
              f"亮度 {_metric(soft.get('centroidHz'), '{:.0f}')}→{_metric(loud.get('centroidHz'), '{:.0f}')}Hz · "
              f"噪比 {_metric(soft.get('hnrDb'), '{:.1f}')}→{_metric(loud.get('hnrDb'), '{:.1f}')}dB")
    anchors = []
    for label, key in (("最气", "airiestLine"), ("最亮", "brightestLine"),
                       ("最毛", "roughestLine"), ("音高最平", "steadiestLine")):
        line = item.get(key)
        if line:
            prefix, fmt = ANCHOR_VALUE_FMT[key]
            value = line.get("value")
            tail = f" {prefix}{fmt.format(value)}" if value is not None else ""
            anchors.append(f"{label} {mmss(line['t'])}「{line['text']}」{tail}")
    if anchors:
        print("锚点: " + " · ".join(anchors))


def clean_note_tracks(payload):
    """保留合法音符；单轨或单条损坏时就地降级，不拖垮整份 notes。"""
    tracks = payload.get("tracks")
    if not isinstance(tracks, dict):
        return {}
    cleaned = {}
    for track, notes_in_track in tracks.items():
        if not isinstance(notes_in_track, list):
            cleaned[track] = []
            continue
        cleaned[track] = [note for note in notes_in_track
                          if isinstance(note, dict) and
                          all(isinstance(note.get(field), (int, float))
                              for field in ("pitch", "start", "end"))]
    return cleaned

def print_journey(data):
    """听感时间轴：把各层数值按歌的时间编成一股辫子，
    让读报告的顺序等于歌走过的路。只变排列，输出仍是机制词。"""
    entries = []
    vocals = (data.get("stemTimeline") or {}).get("vocals") or []
    if vocals:
        entries.append((float(vocals[0][0]), 1, stem_cn("vocals") + "进"))
    for peak in (data.get("salience") or {}).get("peaks", []):
        entries.append((float(peak["t"]), 0, " ·".join(peak.get("evidence") or []) or "注意力峰"))
    motion = data.get("harmonyMotion") or {}
    readable = chords_readable(data.get("chordAnalysis"))
    for item in ((motion.get("states") or []) if readable else []):
        if item["state"] in ("解决", "强解决"):
            entries.append((float(item["start"]), 1, item["state"]))
        elif item["state"] == "延宕":
            entries.append((float(item["start"]), 1, "延宕起"))
    tension = data.get("tension") or {}
    arcs = sorted((a for a in tension.get("arcs") or [] if a["type"] != "平台" and abs(a["deltaPct"]) >= 40),
                  key=lambda a: -abs(a["deltaPct"]))[:4]
    for arc in arcs:
        entries.append((float(arc["start"]), 3, f"{arc['type']} {arc['deltaPct']:+d}%"))
    if readable and tension.get("peak") is not None:
        entries.append((float(tension["peak"]), 0, "张力曲线最高"))
    if readable and tension.get("release") is not None:
        entries.append((float(tension["release"]), 0, "回落"))
    texture = data.get("voiceTexture") or {}
    for label, key in (("最气一句", "airiestLine"), ("最毛一句", "roughestLine")):
        line = texture.get(key)
        if line:
            entries.append((float(line["t"]), 2, f"「{line['text']}」{label}"))
    if not entries:
        return
    entries.sort(key=lambda item: (item[0], item[1]))
    rows = []
    for t, _, text in entries:
        if rows and t - rows[-1][0] <= 3.0:
            rows[-1][1].append(text)
        else:
            rows.append([t, [text]])
    print("—— 听感时间轴 ——")
    for t, texts in rows[:15]:
        print(f"{mmss(t)}  " + " ｜ ".join(dict.fromkeys(texts)))
    if len(rows) > 15:
        print(f"（另 {len(rows) - 15} 拍略，细节在下方分节）")


def timbre_suffix(journey, track):
    if not journey or journey.get("blank"):
        return ""
    track_data = (journey.get("tracks") or {}).get(track) or {}
    events = track_data.get("events") or []
    if track_data.get("steady") or not events:
        return ""
    strongest = max(events, key=lambda event: event.get("magnitude", 0))
    same_span = [event for event in events
                 if event.get("start") == strongest.get("start")
                 and event.get("end") == strongest.get("end")]
    same_span.sort(key=lambda event: event.get("magnitude", 0), reverse=True)
    labels = [TIMBRE_CN[event["feature"]] + event["dir"]
              for event in same_span[:2] if event.get("feature") in TIMBRE_CN]
    return (f" ·{mmss(strongest['start'])}-{mmss(strongest['end'])} " + " ".join(labels)) if labels else ""


def _deep_refresh_plan(data, force, line_file):
    deep = force or "deepVersion" not in data
    notes = force or data.get("notesVersion") != NOTES_VERSION
    chords = force or notes or data.get("chordsVersion") != CHORDS_VERSION
    motion = force or chords or "motionVersion" not in data
    tension = force or deep or motion or "tension" not in data
    lines = (force or deep or motion or data.get("lineVersion") != 2
             or not line_file.exists())
    texture = (force or lines or "voiceTexture" not in data
               or (data.get("voiceTexture") or {}).get("textureVersion") != TEXTURE_VERSION)
    expected = data.get("expectation")
    expectation_refresh = (force or chords or motion or lines or tension
                           or not isinstance(expected, dict)
                           or expected.get("expectVersion") != 3)
    journey = data.get("timbreJourney")
    timbre = (force or deep or not isinstance(journey, dict)
              or journey.get("timbreVersion") != 1)
    return {"deep": deep, "notes": notes, "chords": chords, "motion": motion,
            "tension": tension, "lines": lines, "texture": texture,
            "expectation": expectation_refresh, "timbre": timbre}


def refresh_cross_layer(data, force=False):
    """按 relationVersion 幂等刷新跨层关系缓存。"""
    current = data.get("crossLayer")
    if (not force and isinstance(current, dict)
            and current.get("relationVersion") == relation.RELATION_VERSION):
        return False
    data["crossLayer"] = relation.analyze(data)
    return True


def run_deep(data, cache_dir, force):
    require_deep_dependencies()
    import librosa
    import numpy as np

    destination = cache_dir / "stems"
    result_file = cache_dir / "analysis.json"
    line_file = cache_dir / "lines.json"

    if force:
        for field in ("deepVersion", "stemTimeline", "voiceProfile",
                      "notes", "noteSummary", "notesVersion", "chordAnalysis", "chordsVersion",
                      "harmonyMotion", "motionVersion", "tension", "lineVersion", "voiceTexture",
                      "expectation", "timbreJourney", "crossLayer"):
            data.pop(field, None)
        try:
            line_file.unlink()
        except FileNotFoundError:
            pass

    plan = _deep_refresh_plan(data, force, line_file)
    (needs_deep, needs_notes, needs_chords, needs_motion, needs_tension,
     needs_lines, needs_texture, needs_expectation, needs_timbre) = (
        plan[key] for key in ("deep", "notes", "chords", "motion", "tension",
                              "lines", "texture", "expectation", "timbre"))

    stem_rms_by_track = {}
    if needs_deep:
        if not all((destination / f"{track}.mp3").exists() for track in TRACKS):
            if not memory_gate(4000, "拆轨"):
                raise RuntimeError("深听已停止。")
            split_stems(pathlib.Path(data["sourcePath"]), destination)
        timeline = {}
        vocals_y = None
        vocals_rms = None
        for track in TRACKS:
            y, _ = librosa.load(destination / f"{track}.mp3", sr=SR, mono=True)
            rms = smooth_rms(y, librosa, np)
            stem_rms_by_track[track] = rms
            timeline[track] = active_segments(rms, np)
            if track == "vocals":
                vocals_y, vocals_rms = y, rms
        profile = voice_profile(vocals_y, vocals_rms, timeline["vocals"], librosa, np)
        data.update({"stemTimeline": timeline, "voiceProfile": profile, "deepVersion": 1})
        lyrics.atomic_write_json(result_file, data)

    if needs_timbre:
        data.pop("timbreJourney", None)
        try:
            import timbre_journey
            result = timbre_journey.analyze(destination, data.get("stemTimeline") or {})
            data["timbreJourney"] = result or {"timbreVersion": 1, "blank": True}
        except Exception as error:
            print(f"音色旅程：计算失败（{type(error).__name__}）", file=sys.stderr)

    if needs_notes:
        if not memory_gate(1500, "音符提取"):
            raise RuntimeError("深听已停止。")
        try:
            import notes

            payload, summary = build_note_payload(notes.extract(destination, out_path=None))
            data.update({"notes": payload["tracks"], "noteSummary": summary,
                         "notesVersion": NOTES_VERSION})
            lyrics.atomic_write_json(result_file, data)
            for track, stats in payload["filterStats"].items():
                print(f"音符滤网 {track}: 音域 {stats['range']} / 时长 {stats['duration']} / "
                      f"重叠 {stats['overlap']} / {stats['input']} → {stats['output']}", file=sys.stderr)
        except Exception as error:
            # 本轮失败就不留旧账冒充新结果，和弦层跟着一起撤
            print(f"本轮音符提取失败，不复用旧 notes：{type(error).__name__}", file=sys.stderr)
            for field in ("notes", "noteSummary", "notesVersion", "chordAnalysis", "chordsVersion"):
                data.pop(field, None)
            lyrics.atomic_write_json(result_file, data)

    chord_failed = False
    if needs_chords:
        import chords
        import chroma_chords

        notes_analysis = None
        chroma_analysis = None
        try:
            note_tracks = clean_note_tracks({"tracks": data.get("notes") or {}})
            if not note_tracks:
                raise ValueError("没有可用的音符轨")
            notes_analysis = chords.analyze(note_tracks, data.get("bpm"), data.get("duration"))
            if not notes_analysis.get("spans"):
                raise ValueError("没有可辨识的和弦窗")
        except Exception as error:
            notes_analysis = None
            print(f"音符路和弦警告：{type(error).__name__}", file=sys.stderr)
        try:
            chroma_analysis = chroma_chords.analyze_with_key(destination, data.get("bpm"),
                                                             data.get("duration"))
            if not chroma_analysis.get("spans"):
                raise ValueError("没有 chroma 和弦窗")
        except Exception as error:
            chroma_analysis = None
            print(f"chroma 路和弦警告：{type(error).__name__}", file=sys.stderr)
        try:
            if notes_analysis is None and chroma_analysis is None:
                raise ValueError("双路和弦均不可用")
            analysis = chords.assemble_dual(notes_analysis, chroma_analysis,
                                            data.get("bpm"), data.get("duration"))
            if not analysis.get("spans"):
                raise ValueError("没有可辨识的和弦窗")
            analysis["chordsVersion"] = CHORDS_VERSION
            data.update({"chordAnalysis": analysis, "chordsVersion": CHORDS_VERSION})
            lyrics.atomic_write_json(result_file, data)
        except Exception as error:
            chord_failed = True
            print(f"和弦分析警告：{type(error).__name__}（深听报告照常出）", file=sys.stderr)

    if needs_motion:
        import harmony_motion

        if chord_failed or not data.get("chordAnalysis"):
            data.pop("harmonyMotion", None)
            data.pop("motionVersion", None)
        else:
            motion = harmony_motion.analyze(data["chordAnalysis"])
            if motion is not None:
                data.update({"harmonyMotion": motion, "motionVersion": 1})

    if needs_lines:
        import line_table

        lines = lyric_lines(data)
        vocal_track = destination / "vocals.mp3"
        vocal_segments = (data.get("stemTimeline") or {}).get("vocals") or []
        if lines and vocal_track.exists() and vocal_segments:
            try:
                payload = line_table.build(data, lines, destination, line_file)
                data["lineVersion"] = payload["lineVersion"]
                data["voiceTexture"] = line_table.texture(payload["rows"])
            except Exception as error:
                data.pop("lineVersion", None)
                data.pop("voiceTexture", None)
                print(f"行级表跳过：{type(error).__name__}", file=sys.stderr)
        else:
            data.pop("lineVersion", None)
            data.pop("voiceTexture", None)
            print("没有歌词或没有人声轨，行级表跳过（用 --lyric 配词后可得）", file=sys.stderr)
    elif needs_texture and data.get("lineVersion"):
        import line_table

        data["voiceTexture"] = line_table.texture(read_cache(line_file).get("rows") or [])

    if needs_tension:
        if not stem_rms_by_track:
            for track in TRACKS:
                y, _ = librosa.load(destination / f"{track}.mp3", sr=SR, mono=True)
                stem_rms_by_track[track] = smooth_rms(y, librosa, np)
        data["tension"] = tension_curve(data, data.get("harmonyMotion"), stem_rms_by_track, np)

    if needs_expectation:
        data.pop("expectation", None)
        try:
            import expectation
            result = expectation.analyze(data.get("chordAnalysis"), data.get("harmonyMotion"))
            data["expectation"] = result or {"expectVersion": 3, "blank": True}
            data["expectation"]["loops"] = expectation.loops(
                read_cache(line_file).get("rows") or [], (data.get("tension") or {}).get("curve"))
        except Exception as error:
            print(f"期待层：计算失败（{type(error).__name__}）", file=sys.stderr)

    # 跨层关系只读上面各层；版本不变且上游本轮没重算时原样复用，二跑逐字节一致
    refresh_cross_layer(data, force=(force or needs_deep or needs_chords or needs_motion
                                     or needs_tension or needs_expectation))

    lyrics.atomic_write_json(result_file, data)
    if chord_failed:  # 本轮算砸的和弦不进报告，但已落盘的旧账不动
        data = dict(data)
        data.pop("chordAnalysis", None)
    return data


def print_deep_report(data, cache_dir):
    """三层排列：故事（时间轴）→ 人声 → 地基（和弦/和声运动/张力），编曲底账沉附录。"""
    print(f"=== 深听 ·《{data.get('name') or ''}》===")
    lines = lyric_lines(data)
    print_journey(data)
    print_texture(data)
    print("—— 人声 · 轻唱与爆发 ——")
    profile = data["voiceProfile"]
    if profile is None:
        print("器乐曲，嗓音质地跳过")
    else:
        soft = profile["softWindow"]
        burst = profile["burstWindow"]
        soft_lyric = f" ♪「{line_at(lines, soft['start'])}」" if lines and line_at(lines, soft["start"]) else ""
        burst_lyric = f" ♪「{line_at(lines, burst['start'])}」" if lines and line_at(lines, burst["start"]) else ""
        print(f"轻唱窗(起点 {mmss(soft['start'])})：气息噪声 {soft['breathNoiseRatio'] * 100:.1f}% | 空气感 {soft['airRatio'] * 100:.1f}%{soft_lyric}")
        print(f"爆发窗(起点 {mmss(burst['start'])})：气息噪声 {burst['breathNoiseRatio'] * 100:.1f}% | 空气感 {burst['airRatio'] * 100:.1f}%{burst_lyric}")
        tail = "null" if profile["tailReverb"] is None else f"{profile['tailReverb']:.2f}s"
        print(f"响度倍数 {profile['loudnessRatio']:.1f} | 尾音混响 {tail}")
    print_voice_lines(data, cache_dir, lines)
    print_chords(data)
    print_motion_and_tension(data)
    print("—— 附 · 乐器起止（六轨实测）——")
    timeline = data["stemTimeline"]
    rows = sorted(timeline.items(), key=lambda item: item[1][0][0] if item[1] else float("inf"))
    print(" · ".join(f"{STEM_CN[track]} " +
                     (", ".join(f"{mmss(start)}-{mmss(end)}" for start, end in segments) if segments else "无")
                     for track, segments in rows))
    if data.get("notesVersion"):
        print_melody(data.get("noteSummary") or {},
                     clean_note_tracks({"tracks": data.get("notes") or {}}),
                     data.get("duration") or 0)


def print_voice_axes(data):
    """旧版回落报告的声线坐标：与开场共用同一把锚点尺（9.25 换尺后），返回没印的轴名。"""
    if not data.get("voiceTexture"):
        return []
    try:
        rows, omitted = voice_anchor.voice_axes(
            data["voiceTexture"], voice_anchor.load_anchors(VOICE_ANCHORS), data.get("name"), data.get("artist"))
    except Exception as error:
        print(f"声线坐标：渲染失败（{type(error).__name__}）", file=sys.stderr)
        return []
    for row in rows:
        print(row)
    return omitted


def print_legacy_report(data, cache_dir, lines=()):
    # 三层排列（8.21 既定）：第一层故事、第二层人声、第三层地基，编曲底账沉附录
    print(f"=== 深听 ·《{data.get('name') or ''}》— {data.get('artist') or ''} ===")
    print_journey(data)
    print_texture(data)
    print_voice_axes(data)
    print("—— 嗓子 · 轻唱与爆发 ——")
    profile = data["voiceProfile"]
    if profile is None:
        print("器乐曲，嗓音质地跳过")
    else:
        soft = profile["softWindow"]
        burst = profile["burstWindow"]
        soft_line = f" ♪「{lyrics.line_at(lines, soft['start'])}」" if lines and lyrics.line_at(lines, soft["start"]) else ""
        burst_line = f" ♪「{lyrics.line_at(lines, burst['start'])}」" if lines and lyrics.line_at(lines, burst["start"]) else ""
        print(f"轻唱窗(起点 {mmss(soft['start'])})：气息噪声 {soft['breathNoiseRatio'] * 100:.1f}% | 空气感 {soft['airRatio'] * 100:.1f}%{soft_line}")
        print(f"爆发窗(起点 {mmss(burst['start'])})：气息噪声 {burst['breathNoiseRatio'] * 100:.1f}% | 空气感 {burst['airRatio'] * 100:.1f}%{burst_line}")
        tail = "null" if profile["tailReverb"] is None else f"{profile['tailReverb']:.2f}s"
        print(f"响度倍数 {profile['loudnessRatio']:.1f} | 尾音混响 {tail}")
    print_voice_lines(data, cache_dir, lines)
    print_chords(data)
    print_motion_and_tension(data)
    print("—— 附 · 乐器起止（六轨实测）——")
    timeline = data["stemTimeline"]
    rows = sorted(timeline.items(), key=lambda item: item[1][0][0] if item[1] else float("inf"))
    print(" · ".join(
        f"{stem_cn(track)} " + (", ".join(f"{mmss(start)}-{mmss(end)}" for start, end in segments) if segments else "无")
        + timbre_suffix(data.get("timbreJourney"), track)
        for track, segments in rows
    ))
    if data.get("notesVersion"):
        print_melody(data.get("noteSummary") or {}, clean_note_tracks({"tracks": data.get("notes") or {}}), data.get("duration") or 0)
    print_ambient(data)
    # 旧版自己还印质感中位与旋律线，脚注只点名它真没印的和弦层、和声运动
    print_unprinted(unprinted_items(data, notes=False))


def _screenplay_anchor(t, lyric_lines):
    text = lyrics.line_at(lyric_lines, t) if lyric_lines else None
    return f"{mmss(t)} 「{text}」" if text else mmss(t)


TENSION_REPLAY_WINDOW_S = 8      # 回听点前后对照窗（待听验参数，先取 8 秒）
TENSION_REPLAY_MIN_GAP_S = 20    # 两处回听点最少相隔
TENSION_REPLAY_MIN_SWING = 0.10  # 前升/后落的最小摆动，不到此不算「发生了什么」
BREATH_GAP_MAX_S = 4.0  # 行间隙 ≥ 此值不标气口、也不当下一句气口的参照（气口判定与参照共用）


def tension_replay_points(curve, global_peak_t):
    """从张力曲线挑「上升后回落」的回听点：全局最高点邻近区之外的局部峰，
    前窗最低→峰→后窗最低两头摆动都够才算。最多 2 处，彼此隔 ≥20s。
    只回读曲线不写缓存；曲线太短或太平就留白（9.25）。
    局部峰 = ±8s 窗内严格最高；平顶（窗内有等高点）只认最早那一点（9.25 审计修补）。"""
    points = [(float(t), float(v)) for t, v in (curve or [])]
    if len(points) < 5:
        return []
    candidates = []
    for t, value in points:
        before = [v for bt, v in points if t - TENSION_REPLAY_WINDOW_S <= bt < t]
        after = [v for at, v in points if t < at <= t + TENSION_REPLAY_WINDOW_S]
        if not before or not after:
            continue
        if global_peak_t is not None and abs(t - float(global_peak_t)) < TENSION_REPLAY_MIN_GAP_S:
            continue
        if value <= max(before) or value < max(after):
            continue
        # 曲线只到两位小数：摆幅先圆到两位再比，免得 0.30−0.20 算成 0.0999…
        rise = round(value - min(before), 2)
        fall = round(value - min(after), 2)
        if rise >= TENSION_REPLAY_MIN_SWING and fall >= TENSION_REPLAY_MIN_SWING:
            candidates.append({"t": t, "peak": value, "before": min(before),
                               "after": min(after), "swing": rise + fall,
                               "start": max(points[0][0], t - TENSION_REPLAY_WINDOW_S),
                               "end": min(points[-1][0], t + TENSION_REPLAY_WINDOW_S)})
    picked = []
    for candidate in sorted(candidates, key=lambda item: -item["swing"]):
        if all(abs(candidate["t"] - chosen["t"]) >= TENSION_REPLAY_MIN_GAP_S for chosen in picked):
            picked.append(candidate)
        if len(picked) == 2:
            break
    return sorted(picked, key=lambda item: item["t"])


def _screenplay_events(data):
    """只把既有账编成当前时事件；不推演、不写回分析缓存。"""
    events = []
    add = lambda t, text, kind="ordinary", priority=2: events.append(
        {"t": float(t), "text": text, "kind": kind, "priority": priority})
    readable = chords_readable(data.get("chordAnalysis"))
    motion = data.get("harmonyMotion") or {}
    delays = []
    for item in (motion.get("states") or []) if readable else []:
        state = item.get("state")
        if state == "延宕":
            delays.append(float(item["start"]))
            add(item["start"], "延宕起", "expectation", 0)
        elif state in ("解决", "强解决"):
            # 一口悬起的气只被落地认领一次（8.24 验收：同一回指反刍三遍打回）
            previous = max((t for t in delays if t <= float(item["start"])), default=None)
            if previous is not None:
                delays.remove(previous)
                add(item["start"], f"{state}——{mmss(previous)} 悬起的那口气", "expectation", 0)
            else:
                add(item["start"], state, "expectation", 0)
    expected = data.get("expectation") or {}
    for item in (expected.get("deceptive") or []) if readable else []:
        add(item["t"], f"虚假解决 {item['pair']} 滑走", "expectation", 0)
    # 正片只上主循环（推得动的那组里遍数最多的）；配角组细账都在缓存里
    qualified = [loop for loop in (expected.get("loops") or []) if readable
                 if loop.get("dressDelta", 0) >= 2 or loop.get("loudDelta", 0) >= 3.0
                 or loop.get("tensionClimb", 0) >= 0.15]
    main_loop = max(qualified, key=lambda item: item.get("n", 0)) if qualified else None
    if main_loop:
        span = main_loop.get("spanS") or []
        count = int(main_loop.get("n") or 0)
        if len(span) == 2 and count >= 1:
            text = (main_loop.get("text") or "循环")[:12]
            step = (float(span[1]) - float(span[0])) / max(1, count)
            add(span[0], f"「{text}」第1遍", "loop", 1)
            peak = main_loop.get("tensionPeakT")
            if peak is not None:
                iteration = min(count, max(1, math.ceil((float(peak) - float(span[0])) / step) + 1)) if step else 1
                add(peak, f"「{text}」第{iteration}遍 ｜ 循环张力峰", "loop", 0)
    tension = data.get("tension") or {}
    for arc in tension.get("arcs") or []:
        if arc.get("type") != "平台" and abs(int(arc.get("deltaPct") or 0)) >= 40:
            add(arc["start"], f"力度{arc['type']}起", "tension", 2)
    if readable and tension.get("peak") is not None:
        # 值直接取 curve 上该点（账本绳），好让读者拿它和回听点的峰值对照
        peak_value = next((float(v) for t, v in (tension.get("curve") or [])
                           if float(t) == float(tension["peak"])), None)
        add(tension["peak"], "张力曲线最高" + (f" {peak_value:.2f}" if peak_value is not None else ""),
            "tension", 1)
    if readable:
        for spot in tension_replay_points(tension.get("curve"), tension.get("peak")):
            add(spot["t"],
                f"回听 {mmss(spot['start'])}-{mmss(spot['end'])} ｜ "
                f"张力 {spot['before']:.2f}→{spot['peak']:.2f}→{spot['after']:.2f}",
                "tension", 1)
    if readable and tension.get("release") is not None:
        add(tension["release"], "回落", "tension", 1)
    texture = data.get("voiceTexture") or {}
    # 9.25「最气」下正片：airRatio 跟的是亮度不是气声，数留在 voiceTexture 不印
    for label, key in (("最毛", "roughestLine"),
                       ("最亮", "brightestLine"), ("音高最平", "steadiestLine")):
        line = texture.get(key)
        if line:
            # 极值自带字——行锚被同窗事件带走时，「最毛」也不会挂到别人的词头上
            word = f"「{line['text']}」" if line.get("text") else ""
            add(line["t"], label + word, "voice", 1)
    rows = data.get("_screenplayRows") or []
    for row in rows:
        gap = row.get("gapS")
        previous_gap = row.get("previousGapS")
        if gap is None:
            continue
        if float(gap) < 0:
            add(row["t"], "叠进下句", "voice", 1)
        # 气口窗口（9.25）：<0.6s 是连唱不是事件；≥4s 不标气口。
        # 4s 这个数和下面「人声退/进」的段间静默阈值取了同值，但两边量的不是一个东西：
        # 这里是歌词行间隙（gapS = 下句 LRC 时间戳 − 本句窗内最后一个过阈帧），
        # 那边是分轨人声时间线（stemTimeline.vocals 平滑合并后的段间空档）。
        # 两者不保证一一对应：同一处可能两边都报，也可能两边都不报。
        # gapS 只到两位小数：差值先圆到两位再比 1.5s
        elif 0.6 <= float(gap) < BREATH_GAP_MAX_S and previous_gap is not None \
                and round(abs(float(gap) - float(previous_gap)), 2) >= 1.5:
            add(row["t"], f"气口 {gap:.1f}s", "voice", 1)
    if rows:
        early = [int(row.get("accompStems") or 0) for row in rows[:max(1, len(rows) // 3)]]
        baseline = sorted(early)[len(early) // 2]
        opening = int(rows[0].get("accompStems") or 0)
        candidates = [row for row in rows if int(row.get("accompStems") or 0) >= baseline + 2]
        for row in candidates[:2]:
            count = int(row.get("accompStems") or 0)
            add(row["t"], f"——此刻 {count} 件在场，开场时 {opening} 件", "lookback", 1)
    timeline = data.get("stemTimeline") or {}
    for track, segments in timeline.items():
        if track == "vocals":
            # 段间静默 ≥4s 才算真退场/真进场；换气式的碎进退不上正片
            for index, (start, end) in enumerate(segments):
                gap_before = start - segments[index - 1][1] if index else None
                gap_after = segments[index + 1][0] - end if index + 1 < len(segments) else None
                if index == 0 or (gap_before is not None and gap_before >= 4):
                    add(start, stem_cn("vocals") + "进", "structure", 1)
                if gap_after is None or gap_after >= 4:
                    add(end, stem_cn("vocals") + "退", "structure", 2)
        elif segments:
            add(segments[0][0], f"{stem_cn(track)}首进", "structure", 0)
    # 音色旅程照上；9.25 起不再给它和注意力峰配音符走向（X→Y 跳进上行）——
    # 那是旋律音符层，脚注里写明没印
    journey = data.get("timbreJourney") or {}
    for track, item in (journey.get("tracks") or {}).items():
        for event in item.get("events") or []:
            feature = TIMBRE_CN.get(event.get("feature"))
            if not feature:
                continue
            add(event["start"], f"{stem_cn(track)}{feature}{event.get('dir', '')}", "structure", 0)
    return events


def screenplay_lines(data, cache_dir, lyric_lines=()):
    enriched = dict(data)
    line_payload = read_cache(cache_dir / "lines.json") if cache_dir is not None and data.get("lineVersion") else {}
    source_rows = line_payload.get("rows") or []
    previous = None
    replay_rows = []
    for row in source_rows:
        copied = dict(row)
        copied["previousGapS"] = previous
        # 气口的参照 = 最近一个非间奏停顿：≥4s 的间奏不当参照，免得间奏后第一句
        # 普通停顿和 20s 一比就冒假气口（9.25 审计修补）
        if row.get("gapS") is not None and 0 <= row["gapS"] < BREATH_GAP_MAX_S:
            previous = row["gapS"]
        replay_rows.append(copied)
    enriched["_screenplayRows"] = replay_rows
    events = sorted(_screenplay_events(enriched), key=lambda item: (item["t"], item["priority"]))
    merged = []
    for event in events:
        if merged and event["t"] - merged[-1]["start"] <= 3:
            merged[-1]["texts"].append(event["text"])
            merged[-1]["priority"] = min(merged[-1]["priority"], event["priority"])
            # 只有严格更高优先级才抢行锚——同级互抢会把极值挪到别的字头上
            # （8.24 回归案例：1:20「散」的极值被 1:22 抢锚挂成了「落」，意思正好反）
            if event["priority"] < merged[-1]["anchor_priority"]:
                merged[-1]["t"] = event["t"]
                merged[-1]["anchor_priority"] = event["priority"]
        else:
            merged.append({"start": event["t"], "t": event["t"], "texts": [event["text"]],
                           "priority": event["priority"], "anchor_priority": event["priority"]})
    omitted = max(0, len(merged) - 24)
    if omitted:
        keep_count = 23
        keep = sorted(sorted(range(len(merged)), key=lambda i: (merged[i]["priority"], i))[:keep_count])
        merged = [merged[i] for i in keep]
    rows = []
    for item in merged:
        texts = list(dict.fromkeys(item["texts"]))
        anchor = _screenplay_anchor(item["t"], lyric_lines)
        # 循环短语自己已带歌词；仍保留行首统一锚，方便报时定位。
        rows.append(anchor + " ｜ " + " ｜ ".join(texts))
    if omitted:
        rows.append(f"{mmss(merged[-1]['t'])} ｜ 另 {omitted} 拍入账")
    return rows


def print_screenplay(data, cache_dir, lines=()):
    print("—— 正片 ——")
    rows = screenplay_lines(data, cache_dir, lines)
    print("\n".join(rows) if rows else "证据不足，留白")


def print_opening(data, cache_dir=None):
    """开场只印过了自检的声线坐标（9.25 质感中位行下场）；返回没印的轴名给末尾脚注。
    一根轴都没过时整节不印，连节头也不留。没有声线质感（器乐曲/没词）时这层不适用：
    整节不印、也不进脚注（不是没量准）。"""
    item = data.get("voiceTexture") or {}
    if not item:
        return []
    # 坐标行自己兜底：锚点尺坏了只留白这一块，不把整份剧本版拖回旧版
    try:
        rows, omitted = voice_anchor.voice_axes(
            item, voice_anchor.load_anchors(VOICE_ANCHORS), data.get("name"), data.get("artist"))
    except Exception as error:
        print(f"声线坐标：渲染失败（{type(error).__name__}）", file=sys.stderr)
        rows, omitted = ["声线坐标：渲染失败，留白"], []
    if rows:
        print("—— 开场 · 这是谁的声音 ——")
        for row in rows:
            print(row)
    return omitted


def print_appendix(data, cache_dir, lines=(), omitted_axes=()):
    print("—— 附录 ——")
    print_chords(data)
    print_motion_and_tension(data)
    expected = data.get("expectation") or {}
    # 和弦读不动或调性过不了置信闸时整节不印（没量不能写「无」）；这层空着的事由脚注里的「和声运动」带出
    chord = data.get("chordAnalysis")
    if chords_readable(chord) and not chords_gated(chord):
        print("—— 附 · 期待细账 ——")
        waits = expected.get("waits") or []
        print("等待配对: " + (" · ".join(
            f"{mmss(item['start'])} 延宕 {item['waitS']:.1f}s → {item['resolved']}"
            for item in waits) if waits else "无"))
        deceptive = expected.get("deceptive") or []
        print("虚假解决: " + (" · ".join(
            f"{mmss(item['t'])} {item['pair']}" for item in deceptive) if deceptive else "无"))
    # 9.25：器乐曲整节不印；有人声的只留气息噪声/响度/尾音，空气感（airRatio）退回缓存
    profile = data.get("voiceProfile")
    if not INSTRUMENTAL:
        print("—— 嗓子 · 轻唱与爆发 ——")
        if profile is None:
            print("器乐曲，嗓音质地跳过")
        else:
            soft, burst = profile["softWindow"], profile["burstWindow"]
            soft_text = lyrics.line_at(lines, soft["start"]) if lines else None
            burst_text = lyrics.line_at(lines, burst["start"]) if lines else None
            soft_line = f" ♪「{soft_text}」" if soft_text else ""
            burst_line = f" ♪「{burst_text}」" if burst_text else ""
            print(f"轻唱窗(起点 {mmss(soft['start'])})：气息噪声 {soft['breathNoiseRatio'] * 100:.1f}%{soft_line}")
            print(f"爆发窗(起点 {mmss(burst['start'])})：气息噪声 {burst['breathNoiseRatio'] * 100:.1f}%{burst_line}")
            tail = "null" if profile["tailReverb"] is None else f"{profile['tailReverb']:.2f}s"
            print(f"响度倍数 {profile['loudnessRatio']:.1f} | 尾音混响 {tail}")
    print_voice_lines(data, cache_dir, lines)
    print("—— 附 · 乐器起止（六轨实测）——")
    timeline = data.get("stemTimeline") or {}
    timeline_rows = sorted(timeline.items(), key=lambda item: item[1][0][0] if item[1] else float("inf"))
    print(" · ".join(f"{stem_cn(track)} " + (", ".join(f"{mmss(a)}-{mmss(b)}" for a, b in segments) if segments else "无")
                     + timbre_suffix(data.get("timbreJourney"), track) for track, segments in timeline_rows))
    # 旋律线整节（音符/轮廓/密度/最长静默，全出自 notes）9.25 起不印，notes 照留缓存
    print_ambient(data)
    if chords_readable(data.get("chordAnalysis")):
        print_color_reference(data)
    print_unprinted(unprinted_items(data, omitted_axes))


def unprinted_items(data, omitted_axes=(), notes=True):
    """本歌这次没印的层，按报告出场顺序：声线轴 → 和弦层 → 和声运动 → 旋律音符。
    藏细节可以，藏比例不行：正文不道歉，只在这里点名。器乐曲没有声线层不算没量准，不进。"""
    items = list(omitted_axes)
    if chords_gated(data.get("chordAnalysis")):
        items.append("和弦层")
    if motion_blank(data):
        items.append("和声运动")
    if notes and data.get("notesVersion"):
        items.append("旋律音符")
    return items


def print_unprinted(items):
    if items:
        print(UNPRINTED_HEAD + " · ".join(items))


def print_ambient_block(data):
    """环境声候选（9.8）：同一次 PANNs 推理顺带出的水/风/鸟/雷/人群/火声，只当提示。"""
    print("—— 环境声（候选：模型弱项，只当提示）——")
    if "ambient" not in data:
        print("环境声：旧缓存没算，重听一遍（--force）才有")
    elif not data.get("ambient"):
        print("没听出环境声")
    else:
        for name, item in data["ambient"].items():
            spans = item.get("spans") or []
            where = "、".join(f"{mmss(start)}–{mmss(end)}" for start, end in spans) if spans else "断续"
            print(f"像有{name} · 最高{round(float(item.get('peak', 0)) * 10)}成 · {where}")


def print_ambient(data):
    if not INSTRUMENTAL:
        return
    print_ambient_block(data)


# 和弦色彩对照：写在脚本里，本歌用到的才拉进报告。
# 通识词条，非本歌判断；性质同质感节的标尺行，发布文案须保持原样。
COLOR_QUALITY = (
    ("maj7", "maj7：旧照片的柔光，甜里带一点旧"),
    ("m7", "m7：湿的，像下过小雨的路面"),
    ("sus4", "sus4：悬着不肯落地，等一个没来的字"),
    ("add9", "add9：清晨开窗那口凉气"),
    ("dim", "dim：地板忽然斜了一下"),
    ("aug", "aug：光晕开了，现实浮起来一点"),
    ("7", "属七：想回家的和弦，脚已经朝着门口"),
    ("m", "小三：阴天，不哭"),
)
COLOR_PROG = {
    "4536 王道": "甜而顺的桥，日系情歌的黄昏",
    "1564 流行四和弦": "大路朝天，人人走过的暖",
    "6415": "从阴影里出发，往亮处走",
    "6451 小室": "舞池里的心事，边跳边想",
    "1645 五十年代": "老唱片的慢舞",
    "4156": "先开窗，后叹气",
    "251 二五一": "礼貌的告别，爵士的欠身",
    "小调 4536": "黄昏下过雨",
    "小调 6451": "夜路，一步一步踩实",
}
COLOR_MODE = {"major": "大调：白天的底色", "minor": "小调：夜里的底色"}


def print_color_reference(data):
    """收集本歌真用到的和弦品质/进行/调式，拉出对应色彩词条；一条没有就整节沉默。
    三类词条全出自调性/和弦：0.2 置信闸关掉和弦层时一条不剩，整节连节头不印（9.25）。"""
    if chords_gated(data.get("chordAnalysis")):
        return
    lines_out = []
    key = (data.get("chordAnalysis") or {}).get("key") or ""
    mode = key.split()[-1] if key else ""
    if mode in COLOR_MODE:
        lines_out.append(COLOR_MODE[mode])
    qualities = set()
    for span in (data.get("chordAnalysis") or {}).get("spans") or []:
        chord = span.get("chord") or "?"
        if chord == "?":
            continue
        suffix = chord[1:].lstrip("#")
        for tag, _ in COLOR_QUALITY:
            if tag in ("m", "7"):
                continue  # 歧义位最后单独判
            if tag in suffix:
                qualities.add(tag)
        if "7" in suffix and "maj7" not in suffix and "m7" not in suffix:
            qualities.add("7")
        if suffix.startswith("m") and not suffix.startswith(("maj", "m7")):
            qualities.add("m")
    for tag, text in COLOR_QUALITY:
        if tag in qualities:
            lines_out.append(text)
    dominant = ((data.get("expectation") or {}).get("habit") or {}).get("dominant")
    if dominant in COLOR_PROG:
        lines_out.append(f"{dominant}：{COLOR_PROG[dominant]}")
    if lines_out:
        print("—— 附 · 色彩对照（通识，非本歌判断）——")
        print("\n".join(lines_out))


def print_report(data, cache_dir, lines=()):
    """剧本版入口；整份新版先在内存渲染，失败时不留下半份，直接回落旧版。"""
    import contextlib
    import io
    global INSTRUMENTAL
    INSTRUMENTAL = is_instrumental(data, lines)
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            print(f"=== 深听 ·《{data.get('name') or ''}》— {data.get('artist') or ''} ===")
            omitted_axes = print_opening(data, cache_dir)
            print_screenplay(data, cache_dir, lines)
            print_appendix(data, cache_dir, lines, omitted_axes)
    except Exception as error:
        print(f"正片：渲染失败（{type(error).__name__}），回落旧版报告", file=sys.stderr)
        fallback = io.StringIO()
        with contextlib.redirect_stdout(fallback):
            print_legacy_report(data, cache_dir, lines)
        print(_annotate_main_line(fallback.getvalue()), end="")
        return
    print(_annotate_main_line(output.getvalue()), end="")


def main(argv=None):
    parser = argparse.ArgumentParser(usage="%(prog)s <音频路径> [--deep] [--force] [--lyric 值]")
    parser.add_argument("audio", help="本地音频文件")
    parser.add_argument("--deep", action="store_true", help="运行六轨与嗓音质地深听")
    parser.add_argument("--force", action="store_true", help="忽略缓存并重新计算")
    parser.add_argument("--lyric", help="配歌词：网易云 ID / 链接 / 本地 .lrc·.txt / \"歌名 歌手\"（自动判别）")
    args = parser.parse_args(argv)
    audio_path = pathlib.Path(args.audio).expanduser().resolve()
    if not audio_path.is_file():
        parser.error(f"音频文件不存在：{args.audio}")
    cache_dir = pathlib.Path.cwd() / "ears_cache" / audio_path.stem
    try:
        cached_source = read_cache(cache_dir / "analysis.json").get("sourcePath")
        if cached_source and cached_source != str(audio_path):
            print("缓存来自别的文件，重新分析", file=sys.stderr)
            shutil.rmtree(cache_dir, ignore_errors=True)
        data = ensure_shallow(audio_path, cache_dir, args.force)
        if data.get("sourcePath") != str(audio_path):
            data["sourcePath"] = str(audio_path)
            lyrics.atomic_write_json(cache_dir / "analysis.json", data)
        lyric_failed = False
        if args.lyric:
            try:
                data["lyric"] = lyrics.ensure_lyric(audio_path, cache_dir, args.lyric, args.force)
            except lyrics.LyricError as error:
                print(f"配词失败：{error}（报告照常打印）", file=sys.stderr)
                lyric_failed = True
        print_etiquette(deep=args.deep)
        print_shallow_report(data, cache_dir)
        if args.deep:
            deep_data = run_deep(data, cache_dir, args.force)
            print_report(deep_data, cache_dir, lyric_lines(deep_data))
        return 1 if lyric_failed else 0
    except subprocess.TimeoutExpired as error:
        print(f"失败：子进程超过 {error.timeout} 秒未完成", file=sys.stderr)
    except subprocess.CalledProcessError as error:
        print(f"失败：子进程退出码 {error.returncode}", file=sys.stderr)
    except Exception as error:
        print(str(error), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
