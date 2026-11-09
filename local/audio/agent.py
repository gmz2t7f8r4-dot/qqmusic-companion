"""一起听的分析进程：领任务、取缓冲、跑听骨、回传结果。

录音在另一个独立进程（recorder_proc.py）里跑，因为声音驱动会长期占着 Python 的全局锁，
放同一个进程里会把这里饿死。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent          # local/
PROJECT_DIR = APP_DIR.parent
DATA_DIR = PROJECT_DIR / "local-data"
TMP_DIR = DATA_DIR / "audio-tmp"
STATE_FILE = TMP_DIR / "state.json"

sys.path.insert(0, str(APP_DIR))

from audio.proxy import RecorderProxy  # noqa: E402
from audio.worker import ListenWorker  # noqa: E402
from config import load  # noqa: E402


def setup_logging() -> None:
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(log_dir / "audio.log", when="midnight",
                                       backupCount=7, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> int:
    setup_logging()
    log = logging.getLogger("audio.agent")
    cfg = load()
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    python = Path(cfg.audio_python) if Path(cfg.audio_python).exists() else Path(sys.executable)
    recorder = None
    try:
        recorder = subprocess.Popen(
            [str(python), "-u", str(APP_DIR / "audio" / "recorder_proc.py")],
            cwd=str(APP_DIR), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log.info("录音进程已启动：pid=%s", recorder.pid)
    except Exception:  # noqa: BLE001
        log.exception("录音进程没起来")

    proxy = RecorderProxy(TMP_DIR)
    worker = ListenWorker(cfg, proxy, TMP_DIR)  # type: ignore[arg-type]
    worker.start()
    log.info("一起听进程就绪")
    dup_logged = False

    try:
        while True:
            status = {
                "buffered_seconds": round(proxy.buffered_seconds(), 1),
                "error": proxy.error,
                "jobs_done": worker.jobs_done,
                "recorder_alive": recorder is not None and recorder.poll() is None,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            (TMP_DIR / "agent-status.json").write_text(
                json.dumps(status, ensure_ascii=False), encoding="utf-8")
            if recorder is not None and recorder.poll() is not None:
                code = recorder.returncode
                if code == 2:
                    # 已经有别的录音进程在跑（单实例保护），不要去抢
                    if not dup_logged:
                        log.warning("已有录音进程在运行，这边不重复启动")
                        dup_logged = True
                    recorder = None
                else:
                    log.warning("录音进程退出了（code=%s），重启一次", code)
                    try:
                        recorder = subprocess.Popen(
                            [str(python), "-u", str(APP_DIR / "audio" / "recorder_proc.py")],
                            cwd=str(APP_DIR), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                    except Exception:  # noqa: BLE001
                        log.exception("录音进程重启失败")
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
        if recorder is not None and recorder.poll() is None:
            try:
                recorder.terminate()
                recorder.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
