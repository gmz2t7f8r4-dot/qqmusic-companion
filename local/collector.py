"""QQ 音乐本地采集器：后台读播放状态，整首听完才记一条，上传到云端。

用法：
    python collector.py            正常后台运行
    python collector.py --check    自检：能不能读到 QQ 音乐、云端通不通
    python collector.py --status   打印一次当前状态
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler

import config as config_module
from config import DATA_DIR, LOG_DIR, Config
from media_session import MediaSessionReader
from store import Store
from tracker import Tracker
from uploader import Uploader

log = logging.getLogger("collector")

_MUTEX = None


def setup_logging(cfg: Config, console: bool) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    file_handler = TimedRotatingFileHandler(
        LOG_DIR / "collector.log", when="midnight", backupCount=cfg.log_keep_days, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    if console and sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)


def acquire_single_instance():
    """同名互斥量，防止重复双击启动多个采集器。"""
    global _MUTEX
    kernel32 = ctypes.windll.kernel32
    _MUTEX = kernel32.CreateMutexW(None, False, "Global\\QQMusicCompanionCollector")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return _MUTEX


def _state_extra(store: Store, uploader: Uploader, reader: MediaSessionReader) -> dict:
    return {
        "queue_pending": store.pending_count(),
        "last_upload_ok_at": uploader.last_ok_at,
        "last_upload_error": uploader.last_error,
        "last_media_read_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "media_read_error": reader.last_error,
    }


def run_collector(cfg: Config) -> int:
    if acquire_single_instance() is None:
        log.warning("采集器已经在运行了，这次不重复启动")
        return 0

    pid_file = DATA_DIR / "collector.pid"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="ascii")

    store = Store(DATA_DIR / "queue.db")
    uploader = Uploader(cfg, store)
    tracker = Tracker(store, cfg.complete_ratio, cfg.discontinuity_seconds)
    reader = MediaSessionReader()

    def handle_event(event: dict) -> None:
        store.enqueue_event(event)
        uploader.notify()

    tracker.on_event(handle_event)
    uploader.start()
    log.info("采集器启动：设备=%s，云端=%s", cfg.device_name, cfg.cloud_base_url or "（未配置）")

    last_heartbeat = 0.0
    stop_flag = DATA_DIR / "stop.flag"
    try:
        while True:
            if stop_flag.exists():
                log.info("收到停止标记，准备退出")
                break
            now = time.time()
            snapshot = reader.read()
            tracker.tick(snapshot, now)

            if now - last_heartbeat >= cfg.heartbeat_seconds:
                last_heartbeat = now
                uploader.push_state(tracker.state_payload(_state_extra(store, uploader, reader)))

            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        log.info("收到退出信号")
    finally:
        tracker.finish_all()
        uploader.stop()
        uploader.join(timeout=3)
        # 退出前再尽力上传一次
        try:
            uploader._cycle()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
        store.close()
        try:
            pid_file.unlink(missing_ok=True)
            stop_flag.unlink(missing_ok=True)
        except OSError:
            pass
        log.info("采集器已停止")
    return 0


def cmd_check(cfg: Config) -> int:
    print("=== 配置 ===")
    print("云端地址:", cfg.cloud_base_url or "（未配置，数据只会留在本地）")
    print("采集口令:", "已填写" if cfg.collector_token else "（未填写）")
    print("设备名:", cfg.device_name)
    print("\n=== 读取 QQ 音乐 ===")
    reader = MediaSessionReader()
    snap = reader.read()
    if snap.error:
        print("读取失败:", snap.error)
    elif not snap.present:
        print("没有找到 QQ 音乐的播放会话（QQ 音乐没开，或者没在播放）")
    else:
        print(f"标题: {snap.title}")
        print(f"歌手: {snap.artist}")
        print(f"专辑: {snap.album}")
        print(f"状态: {snap.status}  (Windows 不提供进度和总时长，成品里进度为估算)")

    if cfg.cloud_base_url:
        print("\n=== 云端连通性 ===")
        import urllib.request

        try:
            with urllib.request.urlopen(f"{cfg.cloud_base_url}/health", timeout=8) as resp:
                print("HTTP", resp.status, resp.read().decode("utf-8", "replace")[:200])
        except Exception as exc:  # noqa: BLE001
            print("连不上:", exc)
    return 0


def cmd_status(cfg: Config) -> int:
    store = Store(DATA_DIR / "queue.db")
    reader = MediaSessionReader()
    tracker = Tracker(store, cfg.complete_ratio, cfg.discontinuity_seconds)
    snapshot = reader.read()
    tracker.tick(snapshot, time.time())
    print(json.dumps(tracker.state_payload({"queue_pending": store.pending_count()}), ensure_ascii=False, indent=2))
    store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QQ 音乐本地采集器")
    parser.add_argument("--check", action="store_true", help="自检：读取状态并测试云端连通")
    parser.add_argument("--status", action="store_true", help="打印一次当前状态")
    parser.add_argument("--running", action="store_true", help="只在采集器已运行时返回 0（给启动脚本用）")
    parser.add_argument("--console", action="store_true", help="在前台窗口输出日志（默认写日志文件）")
    args = parser.parse_args(argv)

    if args.running:
        return 0 if acquire_single_instance() is None else 1

    cfg = config_module.load()
    setup_logging(cfg, console=args.console)

    if args.check:
        return cmd_check(cfg)
    if args.status:
        return cmd_status(cfg)
    return run_collector(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
