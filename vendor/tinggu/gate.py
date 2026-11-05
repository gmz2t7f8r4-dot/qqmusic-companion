"""报告层的器乐判定；浅听、深听共用。"""


def is_instrumental(preanalysis: dict | None, lyric_lines) -> bool:
    """没歌词且 PANNs 歌唱组没响过；旧缓存退回只看歌词。"""
    if lyric_lines:
        return False
    if preanalysis is None:
        return True
    return not (preanalysis.get("instruments") or {}).get("歌唱")
