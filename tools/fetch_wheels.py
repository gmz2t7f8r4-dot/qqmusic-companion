"""把需要的小型 Windows 库从 PyPI 直接下载并解压到项目目录。

为什么不直接用 pip：这台机器上的文件保护会拒绝 pip 里的“先写临时文件再替换目标文件”
这一步，导致 pip 解包时报 Permission denied。解压 zip 只是新建文件，不受影响。
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHEELS = ROOT / "local-data" / "wheels"
VENDOR = ROOT / "local" / "vendor"

PLATFORM_TAGS = ("cp313-cp313-win_amd64", "cp313-cp313-win32", "abi3-win_amd64", "py3-none-any")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def fetch_json(pkg: str) -> dict:
    url = f"https://pypi.org/pypi/{pkg}/json"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def choose_wheel(pkg: str, data: dict) -> tuple[str, str] | None:
    candidates = []
    for item in data.get("urls", []):
        fn = item["filename"]
        if not fn.endswith(".whl"):
            continue
        for rank, tag in enumerate(PLATFORM_TAGS):
            if tag in fn:
                candidates.append((rank, fn, item["url"]))
                break
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1], candidates[0][2]


def wheel_metadata(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name.endswith(".dist-info/METADATA"):
                return zf.read(name).decode("utf-8", "replace")
    return ""


def requires(metadata: str) -> list[str]:
    deps = []
    for line in metadata.splitlines():
        if line.lower().startswith("requires-dist:"):
            spec = line.split(":", 1)[1].strip()
            m = re.match(r"([A-Za-z0-9_.\-]+)", spec)
            if not m:
                continue
            name = m.group(1)
            # 跳过可选依赖（extra == "..."）和只在别的平台/版本才需要的依赖
            if "extra" in spec:
                continue
            if "python_version" in spec and "3.13" not in spec:
                continue
            if "sys_platform" in spec and "win32" not in spec:
                continue
            deps.append(name)
    return deps


def main(argv: list[str]) -> int:
    packages: list[str] = []
    target = VENDOR
    with_deps = True
    args = list(argv[1:])
    while args:
        arg = args.pop(0)
        if arg == "--target":
            target = Path(args.pop(0)).resolve()
        elif arg == "--no-deps":
            with_deps = False
        else:
            packages.append(arg)
    wanted = packages or ["winrt-Windows.Media.Control"]
    WHEELS.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    queue = list(wanted)
    downloaded: list[Path] = []

    while queue:
        pkg = queue.pop(0)
        key = norm(pkg)
        if key in seen:
            continue
        seen.add(key)
        try:
            data = fetch_json(pkg)
        except Exception as exc:  # noqa: BLE001
            print(f"[skip] {pkg}: {exc}")
            continue
        picked = choose_wheel(pkg, data)
        if not picked:
            print(f"[skip] {pkg}: 没有适用 win_amd64 / py3 的轮子")
            continue
        filename, url = picked
        dest = WHEELS / filename
        if not dest.exists():
            print(f"[get ] {filename}")
            with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as fh:
                fh.write(resp.read())
        else:
            print(f"[have] {filename}")
        downloaded.append(dest)
        if with_deps:
            for dep in requires(wheel_metadata(dest)):
                if norm(dep) not in seen:
                    queue.append(dep)

    for whl in downloaded:
        with zipfile.ZipFile(whl) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                path = target / member.filename
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists() and path.stat().st_size == member.file_size:
                    continue
                with zf.open(member) as src, open(path, "wb") as dst:
                    dst.write(src.read())
        print(f"[unzip] {whl.name}")

    print(f"\n完成：{len(downloaded)} 个包 -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
