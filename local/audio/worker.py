"""一起听：本地工作线程。向云端领任务 → 把当前这首歌的录音交给听骨 → 回传报告。"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from audio.analyze import analyze
from audio.recorder import LoopbackRecorder

log = logging.getLogger("collector.audio")


class ListenWorker(threading.Thread):
    def __init__(self, cfg, recorder: LoopbackRecorder, audio_dir: Path) -> None:
        super().__init__(name="listen-worker", daemon=True)
        self.cfg = cfg
        self.recorder = recorder
        self.audio_dir = Path(audio_dir)
        self._stopping = threading.Event()
        self.last_error: str | None = None
        self.jobs_done = 0

    def stop(self) -> None:
        self._stopping.set()

    # ---------- HTTP ----------
    def _call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        url = f"{self.cfg.cloud_base_url}{path}"
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.cfg.collector_token}",
                "User-Agent": "qqmusic-companion-collector/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")[:300]
        except Exception as exc:  # noqa: BLE001
            return 0, str(exc)

    # ---------- 主循环 ----------
    def run(self) -> None:
        if not self.cfg.cloud_base_url or not self.cfg.collector_token:
            log.info("没配置云端，听歌分析不领任务")
            return
        log.info("听歌分析已就绪：每 %.0f 秒问一次云端有没有要听的歌", self.cfg.listen_poll_seconds)
        while not self._stopping.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                log.exception("听歌分析循环出错")
            self._stopping.wait(self.cfg.listen_poll_seconds)

    def _tick(self) -> None:
        status, payload = self._call("GET", f"/api/v1/listen/job?device={self.cfg.device_name}")
        if status != 200 or not isinstance(payload, dict):
            if status not in (0, 200):
                self.last_error = f"领任务失败 HTTP {status}"
            return
        job = payload.get("job")
        if not job:
            return

        job_id = job["id"]
        mode = job.get("mode", "shallow")
        buffered = self.recorder.buffered_seconds()

        if buffered < self.cfg.listen_min_seconds:
            self._finish(job_id, False, None, f"还没录到足够的声音（当前 {buffered:.0f} 秒，至少需要 {self.cfg.listen_min_seconds:.0f} 秒）")
            return

        if mode == "deep" and not self.cfg.listen_deep_enabled:
            self._finish(job_id, False, None, "服务端配置为只允许浅听")
            return

        wav = self.audio_dir / f"{job_id}.wav"
        key, title, seconds = self.recorder.snapshot(wav)
        info = self.recorder.track_info()
        lrc_path = None
        try:
            from audio.lyrics_for_analysis import fetch_lrc, write_lrc

            lrc = fetch_lrc(info.get("title") or title, info.get("artist"), info.get("duration"))
            if lrc:
                lrc_path = write_lrc(lrc, self.audio_dir / f"{job_id}.lrc")
        except Exception:  # noqa: BLE001
            log.warning("取歌词失败，按纯音乐处理", exc_info=True)

        log.info("开始分析：%s（%s，%.0f 秒）", title or key or "未知", mode, seconds)
        try:
            outcome = analyze(
                wav, mode,
                venv_python=self.cfg.audio_python,
                tinggu_dir=self.cfg.tinggu_dir,
                lyric=str(lrc_path) if lrc_path else None,
            )
        finally:
            for leftover in (wav, lrc_path):
                if leftover:
                    try:
                        Path(leftover).unlink(missing_ok=True)
                    except OSError:
                        pass

        if outcome.get("ok"):
            self.jobs_done += 1
            self._finish(job_id, True, outcome["report"], None, seconds, title)
            log.info("分析完成：%s", title or key)
        else:
            self._finish(job_id, False, None, outcome.get("error") or "分析失败", seconds, title)
            log.warning("分析失败：%s", outcome.get("error"))

    def _finish(self, job_id: str, ok: bool, report: str | None, error: str | None,
                seconds: float | None = None, title: str | None = None) -> None:
        self._call(
            "POST",
            "/api/v1/listen/result",
            {
                "job_id": job_id,
                "device": self.cfg.device_name,
                "ok": ok,
                "report": report,
                "error": error,
                "seconds": seconds,
                "title": title,
            },
        )
