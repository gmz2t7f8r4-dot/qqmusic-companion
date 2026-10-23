"""歌词查询：走 lrclib 公开接口，按需查、不落库、不编造。

匹配原则：
- 先按歌名 + 歌手 + 专辑 + 时长做精确查询；
- 不行再搜索，用歌名/歌手/专辑/时长综合判断；
- 匹配不确定时返回 ambiguous，并列出候选，而不是随便给一版。
"""

from __future__ import annotations

import json
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://lrclib.net/api"
USER_AGENT = "qqmusic-companion/1.0 (cloud mcp)"


def _norm(text: str | None) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    for ch in " \t\u3000-—_/&,，.。'\"()（）[]【】":
        text = text.replace(ch, "")
    return text


def _artist_ok(wanted: str | None, got: str | None) -> bool:
    a, b = _norm(wanted), _norm(got)
    if not a or not b:
        return True
    if a == b or a in b or b in a:
        return True
    parts_a = {p for p in a.replace("、", "/").replace(",", "/").split("/") if p}
    parts_b = {p for p in b.replace("、", "/").replace(",", "/").split("/") if p}
    return bool(parts_a & parts_b)


def _request(path: str, params: dict) -> tuple[int, object]:
    url = f"{BASE}/{path}?{urllib.parse.urlencode({k: v for k, v in params.items() if v})}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=12) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


def _shape(hit: dict, requested_synced: bool) -> dict:
    synced = hit.get("syncedLyrics")
    plain = hit.get("plainLyrics")
    lyrics = synced if (requested_synced and synced) else (plain or synced)
    return {
        "found": bool(lyrics),
        "status": "ok" if lyrics else "no_lyrics",
        "matched": {
            "title": hit.get("trackName"),
            "artist": hit.get("artistName"),
            "album": hit.get("albumName"),
            "duration_seconds": hit.get("duration"),
            "instrumental": bool(hit.get("instrumental")),
        },
        "synced": bool(synced),
        "lyrics": lyrics,
        "source": "lrclib",
        "note": None,
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

    exact_params = {
        "track_name": title,
        "artist_name": artist,
        "album_name": album,
        "duration": round(duration_seconds) if duration_seconds else None,
    }
    status, payload = _request("get", exact_params)
    if status == 200 and isinstance(payload, dict):
        result = _shape(payload, want_synced)
        if result["found"]:
            result["match_quality"] = "exact"
            return result

    status, hits = _request("search", {"track_name": title, "artist_name": artist, "album_name": album})
    if status != 200 or not isinstance(hits, list):
        return {
            "found": False,
            "status": "error",
            "note": f"歌词服务暂时不可用（HTTP {status}）",
            "source": "lrclib",
        }
    if not hits:
        return {"found": False, "status": "not_found", "note": "lrclib 没有收录这首歌的歌词", "source": "lrclib"}

    candidates = []
    for hit in hits:
        if _norm(hit.get("trackName")) != _norm(title):
            continue
        if not _artist_ok(artist, hit.get("artistName")):
            continue
        candidates.append(hit)

    if not candidates:
        return {
            "found": False,
            "status": "not_found",
            "note": "有同名词条，但歌手对不上，未采用",
            "source": "lrclib",
        }

    def brief(items: list[dict]) -> list[dict]:
        return [
            {
                "title": c.get("trackName"),
                "artist": c.get("artistName"),
                "album": c.get("albumName"),
                "duration_seconds": c.get("duration"),
            }
            for c in items[:5]
        ]

    # 有时长就按时长判：播放器给的真实时长是最可靠的依据
    if duration_seconds:
        ranked = sorted(candidates, key=lambda h: abs(float(h.get("duration") or 0) - float(duration_seconds)))
        best = ranked[0]
        diff = abs(float(best.get("duration") or 0) - float(duration_seconds))
        if diff > 10:
            return {
                "found": False,
                "status": "ambiguous",
                "note": f"找到的最接近版本也差了 {diff:.0f} 秒，可能不是同一版本",
                "candidates": brief(candidates),
                "source": "lrclib",
            }
        result = _shape(best, want_synced)
        result["match_quality"] = "duration"
        result["duration_diff_seconds"] = round(diff, 1)
        if diff > 2.5:
            result["note"] = f"时长相差 {diff:.0f} 秒，歌词可能对应相近但不同的版本"
        return result

    # 没有时长：同专辑 > 唯一候选 > 时长一致，否则算不确定
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
        candidates.sort(key=lambda c: 0 if (want_synced and c.get("syncedLyrics")) else 1)
        result = _shape(candidates[0], want_synced)
        result["match_quality"] = "artist"
        return result

    return {
        "found": False,
        "status": "ambiguous",
        "note": "有多个同名版本，且没有时长可以区分",
        "candidates": brief(candidates),
        "source": "lrclib",
    }
