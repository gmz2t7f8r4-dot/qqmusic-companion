"""把录下来的 wav 交给听骨分析，拿回文字报告。

听骨是第三方开源项目（MIT），源码在 vendor/tinggu；这里只做调用和清理。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DEFAULT_VENV = Path(os.environ.get("LOCALAPPDATA", "")) / "qqmusic-audio" / "venv" / "Scripts" / "python.exe"
DEFAULT_TINGGU = Path(__file__).resolve().parents[2] / "vendor" / "tinggu"

SHALLOW_TIMEOUT = 300
DEEP_TIMEOUT = 1200


def _clean(text: str) -> str:
    """去掉分离模型打出来的进度条和提示行，别混进报告正文。"""
    import re

    noise = (
        re.compile(r"^\s*\d{1,3}%\|"),
        re.compile(r"seconds/s"),
        re.compile(r"^Selected model is a bag"),
        re.compile(r"^Separated tracks will be stored"),
        re.compile(r"^Separating track "),
    )
    lines = [ln for ln in text.splitlines() if not any(p.search(ln) for p in noise)]
    return "\n".join(lines).strip()


def analyze(wav_path: Path, mode: str = "shallow", venv_python: Path | None = None,
            tinggu_dir: Path | None = None, lyric: str | None = None) -> dict:
    venv_python = Path(venv_python) if venv_python else DEFAULT_VENV
    tinggu_dir = Path(tinggu_dir) if tinggu_dir else DEFAULT_TINGGU

    if not venv_python.exists():
        return {"ok": False, "error": f"分析环境不存在：{venv_python}（先跑 安装音频分析.bat）", "report": None}
    if not (tinggu_dir / "ears.py").exists():
        return {"ok": False, "error": f"听骨源码不存在：{tinggu_dir}", "report": None}
    if not wav_path.exists():
        return {"ok": False, "error": "录音文件不存在", "report": None}

    cmd = [str(venv_python), "ears.py", str(wav_path)]
    if mode == "deep":
        cmd.append("--deep")
    if lyric:
        cmd += ["--lyric", lyric]

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # 听骨会写缓存文件，默认按系统编码（Windows 上是 GBK）；
    # 歌名里有韩文/日文时写不进去会崩，这里强制子进程走 UTF-8。
    env["PYTHONUTF8"] = "1"

    # PANNs 的两个模型文件没下全时，用一个占位模块让它直接跳过这一层，
    # 免得听骨在分析途中去下载 330MB 的模型、把任务卡住几十分钟。
    home = Path(os.path.expanduser("~"))
    checkpoints = [home / "panns_data" / "Cnn14_mAP=0.431.pth",
                   home / "panns_data" / "Cnn14_DecisionLevelMax_mAP=0.385.pth"]
    if not all(p.exists() and p.stat().st_size > 300 * 1024 * 1024 for p in checkpoints):
        stub_dir = Path(__file__).resolve().parent / "_stubs"
        env["PYTHONPATH"] = str(stub_dir) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    timeout = DEEP_TIMEOUT if mode == "deep" else SHALLOW_TIMEOUT

    def run_once():
        return subprocess.run(
            cmd, cwd=str(tinggu_dir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )

    try:
        result = run_once()
        # 老缓存可能还是 GBK 写的，读不了就清掉缓存重算一次
        if result.returncode != 0 and "codec can't decode" in (result.stderr or ""):
            cache = tinggu_dir / "ears_cache"
            if cache.exists():
                import shutil

                shutil.rmtree(cache, ignore_errors=True)
            result = run_once()
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"分析超时（{timeout} 秒）", "report": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"分析启动失败：{exc}", "report": None}

    report = _clean(result.stdout or "")
    if result.returncode != 0 and not report:
        return {"ok": False, "error": _clean(result.stderr or "")[-800:], "report": None}

    return {
        "ok": bool(report),
        "error": None if report else (result.stderr or "")[-500:],
        "report": report or None,
        "mode": mode,
    }


if __name__ == "__main__":
    target = Path(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else "shallow"
    outcome = analyze(target, mode)
    if outcome["ok"]:
        print(outcome["report"])
    else:
        print("失败:", outcome["error"])
