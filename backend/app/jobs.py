"""In-process background job queue (no Redis/Celery), state in SQLite.

- One worker thread per lane ("main" for heavy media work, "download" for URL imports), so a
  long download never blocks transcription of another source.
- Progress is written to the Job row (throttled); the UI polls GET /jobs every second.
- Each job has a log file: data/logs/job_<id>.log.
- Cancel: queued jobs are marked cancelled; running jobs get a flag, and any child process
  the handler attached (ffmpeg, transcriber) is killed.
- Resume: on startup, jobs left "running" by a crash/close are re-queued, and leftover temp
  files (*.tmp, *.part) are deleted. Handlers are idempotent and skip finished steps.
"""

from __future__ import annotations

import errno
import logging
import subprocess
import threading
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import select

from app import db, errors
from app.models import JOB_ACTIVE, JOB_CANCELLED, JOB_DONE, JOB_ERROR, JOB_QUEUED, JOB_RUNNING, Job, utcnow

log = logging.getLogger("shortforge.jobs")

LANES = ("main", "download")
PROGRESS_WRITE_INTERVAL = 0.5  # seconds
TEMP_PATTERNS = ("*.tmp", "*.part", "*.tmp.*", "*.ytdl")

Handler = Callable[["JobContext"], None]


class JobCancelled(Exception):
    """Raised inside a handler when the job was cancelled."""


class JobContext:
    """What a handler gets: params, progress/log helpers and cancellation."""

    def __init__(self, queue: JobQueue, job: Job):
        self.queue = queue
        self.job_id: int = job.id  # type: ignore[assignment]
        self.kind = job.kind
        self.source_id = job.source_id
        self.params: dict[str, Any] = dict(job.params or {})
        self.attempt = job.attempts
        self._last_write = 0.0
        self._last_step: str | None = job.step
        self._process: subprocess.Popen | None = None

    # -- progress / log -----------------------------------------------------
    def progress(self, fraction: float, step: str | None = None, force: bool = False) -> None:
        import time

        now = time.monotonic()
        step_changed = step is not None and step != self._last_step
        if not (force or step_changed or fraction >= 1.0 or now - self._last_write >= PROGRESS_WRITE_INTERVAL):
            return
        self._last_write = now
        if step is not None:
            self._last_step = step
        with db.session() as s:
            job = s.get(Job, self.job_id)
            if job is None:
                return
            job.progress = max(0.0, min(1.0, float(fraction)))
            if step is not None:
                job.step = step
            s.add(job)
            s.commit()

    def log(self, message: str) -> None:
        self.queue.append_log(self.job_id, message)

    # -- cancellation -------------------------------------------------------
    def is_cancelled(self) -> bool:
        return self.queue.is_cancel_requested(self.job_id)

    def check_cancelled(self) -> None:
        if self.is_cancelled():
            raise JobCancelled()

    def attach_process(self, proc: subprocess.Popen) -> None:
        """Register a child process so cancel() can kill it."""
        self._process = proc
        self.queue.attach_process(self.job_id, proc)
        if self.is_cancelled():
            _kill(proc)


def _kill(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is None:
            proc.kill()
    except OSError:
        pass


def _is_disk_full(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1))


class JobQueue:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.logs_dir = self.data_dir / "logs"
        self.handlers: dict[str, Handler] = {}
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._wake = {lane: threading.Event() for lane in LANES}
        self._lock = threading.Lock()
        self._cancel_requested: set[int] = set()
        self._processes: dict[int, subprocess.Popen] = {}
        self._log_lock = threading.Lock()

    # -- setup --------------------------------------------------------------
    def register(self, kind: str, handler: Handler) -> None:
        self.handlers[kind] = handler

    def recover(self) -> int:
        """Re-queue jobs interrupted by a crash or app close; delete leftover temp files."""
        count = 0
        with db.session() as s:
            for job in s.exec(select(Job).where(Job.status == JOB_RUNNING)).all():
                job.status = JOB_QUEUED
                job.step = None
                s.add(job)
                count += 1
            s.commit()
        removed = cleanup_temp_files(self.data_dir)
        if count or removed:
            log.info("recovered %d interrupted job(s), removed %d temp file(s)", count, removed)
        return count

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for lane in LANES:
            t = threading.Thread(target=self._worker, args=(lane,), name=f"jobs-{lane}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._lock:
            for job_id, proc in list(self._processes.items()):
                _kill(proc)
        for ev in self._wake.values():
            ev.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()

    # -- API used by routes -------------------------------------------------
    def enqueue(self, kind: str, *, source_id: str | None = None, params: dict[str, Any] | None = None,
                lane: str = "main") -> Job:
        if kind not in self.handlers:
            raise ValueError(f"no handler for job kind {kind!r}")
        if lane not in LANES:
            raise ValueError(f"unknown lane {lane!r}")
        with db.session() as s:
            job = Job(kind=kind, lane=lane, source_id=source_id, params=params or {})
            s.add(job)
            s.commit()
            s.refresh(job)
        self._wake[lane].set()
        return job

    def cancel(self, job_id: int) -> Job:
        with db.session() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise errors.AppError(errors.JOB_NOT_FOUND)
            if job.status == JOB_QUEUED:
                job.status = JOB_CANCELLED
                job.finished_at = utcnow()
                job.error = errors.issue(errors.JOB_CANCELLED)
                s.add(job)
                s.commit()
                s.refresh(job)
                self.append_log(job_id, "cancelled before start")
                return job
            if job.status == JOB_RUNNING:
                with self._lock:
                    self._cancel_requested.add(job_id)
                    proc = self._processes.get(job_id)
                if proc is not None:
                    _kill(proc)
                self.append_log(job_id, "cancel requested")
            return job

    def cancel_for_source(self, source_id: str) -> list[int]:
        with db.session() as s:
            ids = [j.id for j in s.exec(select(Job).where(Job.source_id == source_id, Job.status.in_(JOB_ACTIVE))).all()]
        for job_id in ids:
            self.cancel(job_id)  # type: ignore[arg-type]
        return ids  # type: ignore[return-value]

    def is_cancel_requested(self, job_id: int) -> bool:
        with self._lock:
            return job_id in self._cancel_requested

    def attach_process(self, job_id: int, proc: subprocess.Popen) -> None:
        with self._lock:
            self._processes[job_id] = proc

    # -- logs ---------------------------------------------------------------
    def log_path(self, job_id: int) -> Path:
        return self.logs_dir / f"job_{job_id}.log"

    def append_log(self, job_id: int, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self._log_lock:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            with self.log_path(job_id).open("a", encoding="utf-8") as f:
                for line in str(message).splitlines() or [""]:
                    f.write(f"{stamp}  {line}\n")

    def read_log(self, job_id: int, max_bytes: int = 200_000) -> str:
        path = self.log_path(job_id)
        if not path.exists():
            return ""
        data = path.read_bytes()[-max_bytes:]
        return data.decode("utf-8", errors="replace")

    # -- execution ----------------------------------------------------------
    def _claim(self, lane: str) -> Job | None:
        with self._lock, db.session() as s:
            job = s.exec(
                select(Job).where(Job.lane == lane, Job.status == JOB_QUEUED).order_by(Job.created_at, Job.id)
            ).first()
            if job is None:
                return None
            job.status = JOB_RUNNING
            job.started_at = utcnow()
            job.attempts += 1
            job.error = None
            s.add(job)
            s.commit()
            s.refresh(job)
            return job

    def run_next(self, lane: str = "main") -> Job | None:
        """Claim and run one queued job synchronously. Returns the finished job (or None)."""
        job = self._claim(lane)
        if job is None:
            return None
        self._execute(job)
        with db.session() as s:
            return s.get(Job, job.id)

    def run_all(self, lane: str = "main", limit: int = 100) -> int:
        """Run queued jobs until the lane is empty (tests)."""
        n = 0
        while n < limit and self.run_next(lane) is not None:
            n += 1
        return n

    def _execute(self, job: Job) -> None:
        handler = self.handlers.get(job.kind)
        ctx = JobContext(self, job)
        status, error = JOB_DONE, None
        self.append_log(job.id, f"start {job.kind} (attempt {job.attempts}) params={job.params}")  # type: ignore[arg-type]
        try:
            if handler is None:
                raise RuntimeError(f"no handler for {job.kind}")
            handler(ctx)
            if ctx.is_cancelled():
                raise JobCancelled()
        except JobCancelled:
            status, error = JOB_CANCELLED, errors.issue(errors.JOB_CANCELLED)
        except errors.AppError as exc:
            status, error = JOB_ERROR, exc.to_dict()
            self.append_log(job.id, f"error: {exc.payload}")  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            if ctx.is_cancelled():
                status, error = JOB_CANCELLED, errors.issue(errors.JOB_CANCELLED)
            elif _is_disk_full(exc):
                status, error = JOB_ERROR, errors.issue(errors.DISK_FULL)
                cleanup_temp_files(self.data_dir)
            else:
                status, error = JOB_ERROR, errors.issue(errors.JOB_FAILED)
            self.append_log(job.id, traceback.format_exc())  # type: ignore[arg-type]
            log.warning("job %s (%s) failed: %s", job.id, job.kind, exc)
        finally:
            with self._lock:
                self._cancel_requested.discard(job.id)  # type: ignore[arg-type]
                self._processes.pop(job.id, None)  # type: ignore[arg-type]
        with db.session() as s:
            row = s.get(Job, job.id)
            if row is not None:
                row.status = status
                row.error = error
                row.finished_at = utcnow()
                if status == JOB_DONE:
                    row.progress = 1.0
                s.add(row)
                s.commit()
        self.append_log(job.id, f"end: {status}")  # type: ignore[arg-type]

    def _worker(self, lane: str) -> None:
        while not self._stop.is_set():
            try:
                job = self._claim(lane)
            except Exception:  # noqa: BLE001  (e.g. DB briefly locked)
                log.exception("claim failed")
                job = None
            if job is None:
                self._wake[lane].wait(timeout=1.0)
                self._wake[lane].clear()
                continue
            self._execute(job)


def cleanup_temp_files(data_dir: Path) -> int:
    """Delete leftover temp files inside per-source folders and the cache (not models/)."""
    removed = 0
    if not data_dir.exists():
        return 0
    roots = [p for p in data_dir.iterdir() if p.is_dir() and (p.name.startswith("src_") or p.name == "cache")]
    for root in roots:
        for pattern in TEMP_PATTERNS:
            for path in root.rglob(pattern):
                try:
                    if path.is_file():
                        path.unlink()
                        removed += 1
                except OSError:
                    pass
    return removed


# The app-wide queue (created in app.main's lifespan)
_queue: JobQueue | None = None


def set_queue(queue: JobQueue | None) -> None:
    global _queue
    _queue = queue


def get_queue() -> JobQueue:
    if _queue is None:
        raise RuntimeError("job queue not initialised")
    return _queue
