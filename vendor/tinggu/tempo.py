"""人感速度（9.25）：把 beat_track 的原测 BPM 按倍数折进 [60, 120)。

librosa 的节拍跟踪常把慢歌报成两倍（79→161、70→144 这类），它给的 T 与 T/2
证据分不出哪个是人感的拍——所以这是先验，不是测量。在 13 首有人工标注速度的歌上，
按倍数折进这个区间后对的从 3 首升到 11 首，原本对的没有一首变错。
盲区：真正快的歌（club remix 这类）会被折成一半。原测值照旧留在缓存 bpm 字段，
和弦层按它切拍，不动；只在报告里用折后值，并注明原测。
"""


def felt_bpm(raw):
    """原测 → 人感；非正数/非数字原样返回。"""
    if not isinstance(raw, (int, float)) or raw <= 0:
        return raw
    tempo = float(raw)
    while tempo >= 120.0:
        tempo /= 2.0
    while tempo < 60.0:
        tempo *= 2.0
    return round(tempo)
