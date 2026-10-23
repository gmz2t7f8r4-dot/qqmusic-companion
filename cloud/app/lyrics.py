"""歌词查询：走 lrclib 公开接口，按需查、不落库、不编造。

匹配原则：
- 先按歌名 + 歌手 + 专辑 + 时长做精确查询；
- 歌名里的版本后缀（Live、Remix、feat.、Remastered 等）先剥掉再匹配，
  但原文也会试一遍，避免把真的带这些词的歌剥没了；
- 不行再搜索，用歌名/歌手/专辑/时长综合判断；
- 匹配不确定时返回 ambiguous，并列出候选，而不是随便给一版。
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://lrclib.net/api"
USER_AGENT = "qqmusic-companion/1.0 (cloud mcp)"

# 出现在括号里或 " - " 后面，就说明那是版本说明，不是歌名的一部分
VERSION_MARKERS = (
    "live", "remix", "remaster", "version", "ver.", "edit", "acoustic", "demo",
    "cover", "instrumental", "inst.", "karaoke", "feat", "ft.", "mix", "session",
    "mono", "stereo", "bonus", "deluxe", "single", "radio", "extended",
    "现场", "演唱会", "伴奏", "翻唱", "重制", "重置", "纯音乐", "女生版", "男生版", "dj版",
)

# 时长差在这个范围内，认为就是同一个版本
DURATION_SAME = 3.0
# 超过这个差值就不算同一首
DURATION_LIMIT = 15.0


def _norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    for ch in " \t\u3000-—_/&,，.。'\"()（）[]【】·、":
        text = text.replace(ch, "")
    return text


def strip_versions(title: str) -> str:
    """把 "歌名 (Live)"、"歌名 - Remastered 2011"、"歌名 feat. X" 这类后缀去掉。"""
    text = unicodedata.normalize("NFKC", title or "")
    if not text:
        return ""

    def replace_bracket(match: re.Match) -> str:
        inner = match.group(1).lower()
        return "" if any(marker in inner for marker in VERSION_MARKERS) else match.group(0)

    text = re.sub(r"[\(\[（【]([^\)\]）】]{0,60})[\)\]）】]", replace_bracket, text)

    # "标题 - 版本说明"
    dash = re.match(r"^(.*?)\s+[-–—]\s+(.*)$", text)
    if dash and any(marker in dash.group(2).lower() for marker in VERSION_MARKERS):
        text = dash.group(1)

    # 任意位置的 feat. / ft.
    text = re.sub(r"\s*(?:feat\.?|ft\.)\s+[^,;/]+", "", text, flags=re.IGNORECASE)
    return text.strip(" -–—·")


def _artist_parts(value: str | None) -> set[str]:
    text = _norm(value)
    for sep in ("、", ",", "&", "feat", "ft.", "with", "×", "x"):
        text = text.replace(sep, "/")
    return {part for part in text.split("/") if part}


def _artist_ok(wanted: str | None, got: str | None) -> bool:
    if not wanted or not got:
        return True
    a, b = _norm(wanted), _norm(got)
    if a == b or a in b or b in a:
        return True
    return bool(_artist_parts(wanted) & _artist_parts(got))


def _title_ok(query: str, clean_query: str, candidate: str | None) -> bool:
    cand = _norm(candidate)
    if not cand:
        return False
    if cand in (_norm(query), _norm(clean_query)):
        return True
    # 一边带版本后缀、一边没有，也算对上
    return cand == _norm(strip_versions(candidate or ""))


def _request(path: str, params: dict, timeout: int = 15) -> tuple[int, object]:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    request = urllib.request.Request(
        f"{BASE}/{path}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


def _shape(hit: dict, requested_synced: bool) -> dict:
    synced = hit.get("syncedLyrics")
    plain = hit.get("plainLyrics")
    lyrics = synced if (requested_synced and synced) else (plain or synced)
    instrumental = bool(hit.get("instrumental"))
    if not lyrics and instrumental:
        status = "instrumental"
    elif lyrics:
        status = "ok"
    else:
        status = "no_lyrics"
    return {
        "found": bool(lyrics),
        "status": status,
        "matched": {
            "title": hit.get("trackName"),
            "artist": hit.get("artistName"),
            "album": hit.get("albumName"),
            "duration_seconds": hit.get("duration"),
            "instrumental": instrumental,
        },
        "synced": bool(synced),
        "lyrics": lyrics,
        "source": "lrclib",
        "note": None,
    }


def _note(item: dict) -> dict:
    return {
        "title": item.get("trackName"),
        "artist": item.get("artistName"),
        "album": item.get("albumName"),
        "duration_seconds": item.get("duration"),
    }


def fetch_lyrics(
    title: str,
    artist: str | None = None,
    album: str | None = None,
    duration_seconds: float | None = None,
    want_synced: bool = True,
) -> dict:
    title = (title or "").strip()
    if not title:
        return {"found": False, "status": "missing_title", "note": "没有提供歌曲名，也没有可用的当前歌曲"}

    clean = strip_versions(title) or title
    variants = [title] if _norm(clean) == _norm(title) else [title, clean]

    # 1) 精确查询：原文一遍，剥掉版本后缀再来一遍
    for variant in variants:
        status, payload = _request(
            "get",
            {
                "track_name": variant,
                "artist_name": artist,
                "album_name": album,
                "duration": round(duration_seconds) if duration_seconds else None,
            },
        )
        if status == 200 and isinstance(payload, dict):
            result = _shape(payload, want_synced)
            if result["found"] or result["status"] == "instrumental":
                result["match_quality"] = "exact"
                if variant != title:
                    result["note"] = f"按去掉版本后缀的「{variant}」匹配"
                return result

    # 2) 搜索：优先用剥掉后缀的名字，命中率更高
    hits: list[dict] = []
    for variant in [clean, title]:
        status, payload = _request("search", {"track_name": variant, "artist_name": artist, "album_name": album})
        if status != 200 or not isinstance(payload, list):
            return {
                "found": False,
                "status": "error",
                "note": f"歌词服务暂时不可用（HTTP {status}）",
                "source": "lrclib",
            }
        hits.extend(payload)
        if hits:
            break

    if not hits:
        return {"found": False, "status": "not_found", "note": "lrclib 没有收录这首歌的歌词", "source": "lrclib"}

    candidates = [
        hit
        for hit in hits
        if _title_ok(title, clean, hit.get("trackName")) and _artist_ok(artist, hit.get("artistName"))
    ]

    if not candidates:
        return {
            "found": False,
            "status": "not_found",
            "note": "有相关词条，但歌名或歌手对不上，未采用",
            "source": "lrclib",
        }

    # 有歌词的优先；需要的带时间轴时，带时间轴的优先
    candidates.sort(key=lambda h: (0 if h.get("syncedLyrics") else 1) if want_synced else (0 if h.get("plainLyrics") else 1))

    # 3) 有时长就按时长判：播放器给的真实时长最可靠
    if duration_seconds:
        ranked = sorted(candidates, key=lambda h: abs(float(h.get("duration") or 0) - float(duration_seconds)))
        best = ranked[0]
        best_duration = float(best.get("duration") or 0)
        diff = abs(best_duration - float(duration_seconds)) if best_duration else None

        if diff is not None and diff > DURATION_LIMIT:
            return {
                "found": False,
                "status": "ambiguous",
                "note": f"找到的最接近版本也差了 {diff:.0f} 秒，可能不是同一版本",
                "candidates": [_note(c) for c in candidates[:5]],
                "source": "lrclib",
            }

        result = _shape(best, want_synced)
        result["match_quality"] = "duration"
        if diff is not None:
            result["duration_diff_seconds"] = round(diff, 1)
            if diff > DURATION_SAME:
                result["note"] = f"时长相差 {diff:.0f} 秒，歌词可能对应相近但不同的版本"
        return result

    # 4) 没有时长：同专辑 > 唯一候选 > 时长彼此接近，否则算不确定
    if album:
        same_album = [c for c in candidates if _norm(c.get("albumName")) == _norm(album)]
        if len(same_album) == 1:
            result = _shape(same_album[0], want_synced)
            result["match_quality"] = "album"
            return result

    if len(candidates) == 1:
        result = _shape(candidates[0], want_synced)
        result["match_quality"] = "artist"
        return result

    known = [float(c.get("duration") or 0) for c in candidates if c.get("duration")]
    if known and max(known) - min(known) <= 5:
        result = _shape(candidates[0], want_synced)
        result["match_quality"] = "artist"
        return result

    return {
        "found": False,
        "status": "ambiguous",
        "note": "有多个同名版本，且没有时长可以区分",
        "candidates": [_note(c) for c in candidates[:5]],
        "source": "lrclib",
    }
