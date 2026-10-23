"""歌词匹配的验证脚本（会联网访问 lrclib）。

跑法：python tests/test_lyrics.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows 控制台默认 GBK，遇到带变音符号的歌手名会崩，这里强制 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "cloud"))

from app.lyrics import fetch_lyrics, strip_versions  # noqa: E402

STRIP_CASES = [
    ("SOMETHING AIN'T RIGHT", "SOMETHING AIN'T RIGHT"),
    ("晴天 (Live)", "晴天"),
    ("稻香 - Remastered 2011", "稻香"),
    ("Nasty Girl Remix (feat. Chloe)", "Nasty Girl Remix"),
    ("富士山下（现场版）", "富士山下"),
    ("七里香 (伴奏)", "七里香"),
]

LOOKUP_CASES = [
    ("SOMETHING AIN'T RIGHT", "XG", None, 198),
    ("富士山下", "陈奕迅", None, 257),
    ("Nasty Girl Remix (feat. Chloe)", "Tinashe/Chlöe", None, None),
    ("晴天 (Live)", "周杰伦", None, 269),
    ("稻香 - Remastered 2011", "周杰伦", None, 223),
]


def main() -> int:
    failures = 0

    print("=== 版本后缀剥离 ===")
    for raw, want in STRIP_CASES:
        got = strip_versions(raw)
        ok = got == want
        failures += 0 if ok else 1
        print(f"[{'OK ' if ok else 'FAIL'}] {raw!r} -> {got!r}（期望 {want!r}）")

    print("\n=== 实际查询 ===")
    for title, artist, album, duration in LOOKUP_CASES:
        result = fetch_lyrics(title, artist, album, duration, want_synced=True)
        quality = result.get("match_quality", "-")
        matched = result.get("matched") or {}
        print(f"[{result['status']:<10}] {title[:32]!r} -> {matched.get('title')!r} / {matched.get('artist')!r} "
              f"quality={quality} synced={result.get('synced')} 长度={len(result.get('lyrics') or '')}")
        if result["status"] not in ("ok",):
            failures += 1
            print("    note:", result.get("note"))

    print("\n全部通过" if not failures else f"\n{failures} 项没通过")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
