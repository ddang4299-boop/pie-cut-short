"""Pipeline orchestration: job handlers and Source state helpers.

Job kinds:
- "download": URL import via yt-dlp, then preflight. The source then waits for Start.
- "pipeline": runs the steps in models.STEPS from params["from_step"], skipping finished ones,
  so a re-queued job (after a crash or app restart) resumes where it stopped.

Each step stores {state, progress, error, stage, notices} in Source.steps (see models.py).
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app import db, errors
from app.config import get_settings
from app.jobs import JobCancelled, JobContext, JobQueue
from app.models import (
    JOB_CANCELLED,
    SOURCE_DOWNLOADING,
    SOURCE_ERROR,
    SOURCE_NEW,
    SOURCE_PROCESSING,
    SOURCE_READY,
    STEP_DONE_STATES,
    STEPS,
    Job,
    Source,
    empty_steps,
    utcnow,
)
from pipeline import ingest, preflight, transcribe

BACKEND_DIR = Path(__file__).resolve().parents[1]
IMPLEMENTED_STEPS = ("preflight", "proxy", "transcribe")  # analyze + moments arrive in Phase 2
STEP_WEIGHTS = {"preflight": 0.05, "proxy": 0.30, "transcribe": 0.65, "analyze": 0.0, "moments": 0.0}
PROXY_VERSION = 1

StepFn = Callable[[JobContext, Source, "StepReporter"], str]


# --------------------------------------------------------------------------
# Source helpers
# --------------------------------------------------------------------------
def new_source_id() -> str:
    return "src_" + secrets.token_hex(4)


def source_dir(source_id: str) -> Path:
    return get_settings().data_path / source_id


def load_source(source_id: str) -> Source:
    with db.session() as s:
        src = s.get(Source, source_id)
        if src is None:
            raise errors.AppError(errors.SOURCE_NOT_FOUND)
        return src


def update_source(source_id: str, **fields: Any) -> Source:
    with db.session() as s:
        src = s.get(Source, source_id)
        if src is None:
            raise errors.AppError(errors.SOURCE_NOT_FOUND)
        for key, value in fields.items():
            setattr(src, key, value)
        src.updated_at = utcnow()
        s.add(src)
        s.commit()
        s.refresh(src)
        return src


def set_step(source_id: str, step: str, **changes: Any) -> None:
    with db.session() as s:
        src = s.get(Source, source_id)
        if src is None:
            raise errors.AppError(errors.SOURCE_NOT_FOUND)
        steps = copy.deepcopy(src.steps or empty_steps())
        entry = steps.setdefault(step, {"state": "pending", "progress": 0.0, "error": None})
        entry.update(changes)
        src.steps = steps
        src.updated_at = utcnow()
        s.add(src)
        s.commit()


def reset_steps(source_id: str, from_step: str) -> None:
    """Mark `from_step` and every later step as pending again."""
    if from_step not in STEPS:
        raise errors.AppError(errors.INVALID_STEP, step=from_step)
    src = load_source(source_id)
    steps = copy.deepcopy(src.steps or empty_steps())
    for step in STEPS[STEPS.index(from_step):]:
        steps[step] = {"state": "pending", "progress": 0.0, "error": None}
    update_source(source_id, steps=steps)


def first_unfinished_step(src: Source) -> str | None:
    for step in IMPLEMENTED_STEPS:
        if (src.steps or {}).get(step, {}).get("state") not in STEP_DONE_STATES:
            return step
    return None


def _issue_error(issue: dict[str, Any]) -> errors.AppError:
    """Turn a stored issue dict back into an AppError."""
    entry = errors.REGISTRY.get(issue.get("code", ""), errors.JOB_FAILED)
    params = {k: v for k, v in (issue.get("params") or {}).items() if k in entry.params}
    for missing in set(entry.params) - set(params):
        params[missing] = ""
    return errors.AppError(entry, **params)


class StepReporter:
    """Writes step progress to Source.steps and overall progress to the Job (throttled)."""

    def __init__(self, ctx: JobContext, source_id: str, step: str, base: float, weight: float):
        self.ctx, self.source_id, self.step = ctx, source_id, step
        self.base, self.weight = base, weight
        self._last = 0.0
        self.notices: list[dict[str, Any]] = []

    def __call__(self, fraction: float, stage: str | None = None) -> None:
        self.ctx.check_cancelled()
        fraction = max(0.0, min(1.0, fraction))
        now = time.monotonic()
        if stage is None and fraction < 1.0 and now - self._last < 0.5:
            return
        self._last = now
        changes: dict[str, Any] = {"progress": round(fraction, 4)}
        if stage is not None:
            changes["stage"] = stage
        set_step(self.source_id, self.step, **changes)
        self.ctx.progress(self.base + self.weight * fraction, step=self.step, force=stage is not None)

    def notice(self, code: errors.ErrorCode, **params: Any) -> None:
        self.notices.append(errors.issue(code, **params))


# --------------------------------------------------------------------------
# Preflight (also called synchronously right after an upload/import)
# --------------------------------------------------------------------------
def run_source_preflight(source_id: str) -> Source:
    settings = get_settings()
    src = load_source(source_id)
    if not src.media_path:
        raise errors.AppError(errors.FILE_NOT_FOUND, name=src.name)
    media = Path(src.media_path)
    set_step(source_id, "preflight", state="running", progress=0.0, error=None)
    result = preflight.run_preflight(media, settings.data_path, settings.ffmpeg_bin, settings.ffprobe_bin, settings.device)
    fields: dict[str, Any] = {"preflight": result}
    probe = result.get("probe") or {}
    if probe:
        video = probe.get("video") or {}
        fields.update(
            duration=probe.get("duration"),
            size_bytes=probe.get("size_bytes"),
            fps=video.get("fps") or None,
            width=video.get("display_width") or None,
            height=video.get("display_height") or None,
            has_audio=probe.get("audio") is not None,
        )
    if media.is_file():
        fields["file_hash"] = ingest.quick_hash(media)
    update_source(source_id, **fields)

    if result["ok"]:
        if video := probe.get("video"):
            thumb = source_dir(source_id) / "thumb.jpg"
            thumb.parent.mkdir(parents=True, exist_ok=True)
            at = min(max(1.0, (probe.get("duration") or 0) * 0.1), 30.0)
            if video and ingest.make_thumbnail(media, thumb, at=at, ffmpeg=settings.ffmpeg_bin):
                update_source(source_id, thumbnail_path=str(thumb))
        set_step(source_id, "preflight", state="done", progress=1.0, error=None)
    else:
        first_error = next((i for i in result["issues"] if i["severity"] == "error"), None)
        set_step(source_id, "preflight", state="error", progress=1.0, error=first_error)
    return load_source(source_id)


def step_preflight(ctx: JobContext, src: Source, report: StepReporter) -> str:
    report(0.1, stage="checking")
    updated = run_source_preflight(src.id)
    if not (updated.preflight or {}).get("ok"):
        issue = next((i for i in updated.preflight["issues"] if i["severity"] == "error"), None)  # type: ignore[index]
        raise _issue_error(issue or errors.issue(errors.JOB_FAILED))
    return "done"


# --------------------------------------------------------------------------
# Proxy
# --------------------------------------------------------------------------
def step_proxy(ctx: JobContext, src: Source, report: StepReporter) -> str:
    settings = get_settings()
    folder = source_dir(src.id)
    folder.mkdir(parents=True, exist_ok=True)
    proxy = folder / "proxy.mp4"
    sidecar = folder / "proxy.json"
    wanted = {"version": PROXY_VERSION, "file_hash": src.file_hash}
    if proxy.exists() and sidecar.exists():
        try:
            if {k: v for k, v in json.loads(sidecar.read_text("utf-8")).items() if k in wanted} == wanted:
                ctx.log("proxy is up to date (cached)")
                update_source(src.id, proxy_path=str(proxy))
                return "done"
        except (OSError, ValueError):
            pass

    if not src.width or not src.height or not src.media_path:
        raise errors.AppError(errors.PROXY_FAILED)
    device = preflight.detect_device(settings.device)
    encoder = preflight.detect_encoder(preflight.tool_info(settings.ffmpeg_bin), device)
    report(0.0, stage="encoding")
    try:
        info = ingest.make_proxy(
            Path(src.media_path), proxy,
            width=src.width, height=src.height, fps=src.fps or 30.0, duration=src.duration or 0.0,
            has_audio=src.has_audio, encoder=encoder, ffmpeg=settings.ffmpeg_bin,
            on_progress=lambda f: report(f), on_process=ctx.attach_process, log=ctx.log,
        )
    except ingest.FFmpegError as exc:
        ctx.check_cancelled()
        ctx.log(f"proxy failed: {exc}\n{exc.stderr_tail}")
        raise errors.AppError(errors.PROXY_FAILED) from exc
    sidecar.write_text(json.dumps({**wanted, **info}), encoding="utf-8")
    ctx.log(f"proxy written: {info}")
    fields: dict[str, Any] = {"proxy_path": str(proxy)}
    thumb = folder / "thumb.jpg"
    if not thumb.exists():
        at = min(max(1.0, (src.duration or 0) * 0.1), 30.0)
        if ingest.make_thumbnail(proxy, thumb, at=at, ffmpeg=settings.ffmpeg_bin):
            fields["thumbnail_path"] = str(thumb)
    update_source(src.id, **fields)
    return "done"


# --------------------------------------------------------------------------
# Transcribe
# --------------------------------------------------------------------------
def transcripts_cache_dir() -> Path:
    return get_settings().data_path / "cache" / "transcripts"


def transcript_path(source_id: str) -> Path:
    return source_dir(source_id) / "transcript.json"


def whisper_device() -> str:
    """ctranslate2 supports CUDA or CPU (no Apple MPS)."""
    return "cuda" if preflight.detect_device(get_settings().device) == "cuda" else "cpu"


def run_transcriber(
    ctx: JobContext,
    audio: Path,
    out: Path,
    *,
    model: str,
    device: str,
    language: str,
    report: StepReporter,
) -> None:
    """Run `python -m pipeline.transcribe` as a child process and relay its JSON events."""
    settings = get_settings()
    cmd = [sys.executable, "-m", "pipeline.transcribe", str(audio), "--out", str(out), "--model", model,
           "--device", device, "--language", language, "--models-dir", str(settings.models_path),
           "--ffmpeg", settings.ffmpeg_bin]
    ctx.log("$ " + " ".join(cmd))
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
    proc = subprocess.Popen(
        cmd, cwd=str(BACKEND_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    ctx.attach_process(proc)
    stderr_tail: list[str] = []

    def drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                stderr_tail.append(line)
                del stderr_tail[:-40]

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    error_message: str | None = None
    assert proc.stdout is not None
    for line in proc.stdout:
        try:
            event = json.loads(line)
        except ValueError:
            ctx.log(line.rstrip())
            continue
        kind = event.get("event")
        if kind == "progress":
            report(0.05 + 0.95 * float(event.get("progress", 0)))
        elif kind == "stage":
            ctx.log(f"stage: {event}")
            report(0.05 if event.get("stage") != "transcribing" else 0.06, stage=event.get("stage"))
        elif kind == "warning":
            code = errors.REGISTRY.get(event.get("code", ""))
            if code is not None and not code.params:
                report.notice(code)
        elif kind == "error":
            error_message = str(event.get("message", ""))
            ctx.log("transcriber error: " + error_message)
        else:
            ctx.log(f"transcriber: {event}")
    proc.wait()
    t.join(timeout=5)
    if stderr_tail:
        ctx.log("\n".join(stderr_tail[-20:]))
    ctx.check_cancelled()
    if proc.returncode != 0 or not out.exists():
        detail = (error_message or (stderr_tail[-1] if stderr_tail else f"exit code {proc.returncode}"))[:300]
        raise errors.AppError(errors.TRANSCRIBE_FAILED, detail=detail)


def step_transcribe(ctx: JobContext, src: Source, report: StepReporter) -> str:
    settings = get_settings()
    if not src.has_audio:
        report.notice(errors.TRANSCRIBE_SKIPPED_NO_AUDIO)
        return "skipped"
    option = src.language_override if src.language_override in transcribe.LANGUAGE_OPTIONS else "auto"
    model = settings.whisper_model
    key = transcribe.cache_key(src.file_hash or src.id, model, option)
    cache_dir = transcripts_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{key}.json"
    dest = transcript_path(src.id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    force = bool(ctx.params.get("force")) and ctx.params.get("from_step") == "transcribe"

    if cached.exists() and not force:
        ctx.log(f"transcript cache hit {cached.name}")
    else:
        audio = source_dir(src.id) / "audio_16k.wav"
        if not audio.exists():
            report(0.0, stage="extracting_audio")
            try:
                ingest.extract_audio(Path(src.media_path or ""), audio, duration=src.duration,
                                     ffmpeg=settings.ffmpeg_bin, on_process=ctx.attach_process,
                                     on_progress=lambda f: report(0.05 * f), log=ctx.log)
            except ingest.FFmpegError as exc:
                ctx.check_cancelled()
                raise errors.AppError(errors.TRANSCRIBE_FAILED, detail=str(exc)[:300]) from exc
        tmp = cache_dir / f"{key}.json.tmp"
        run_transcriber(ctx, audio, tmp, model=model, device=whisper_device(), language=option, report=report)
        tmp.replace(cached)

    shutil.copyfile(cached, dest)
    data = json.loads(dest.read_text("utf-8"))
    if not data.get("words"):
        report.notice(errors.NO_SPEECH_FOUND)
    if data.get("device") == "cpu" and whisper_device() == "cuda":
        if not any(n["code"] == errors.TRANSCRIBE_CPU_FALLBACK.code for n in report.notices):
            report.notice(errors.TRANSCRIBE_CPU_FALLBACK)
    update_source(src.id, language=data.get("language"))
    return "done"


STEP_FUNCS: dict[str, StepFn] = {
    "preflight": step_preflight,
    "proxy": step_proxy,
    "transcribe": step_transcribe,
}


# --------------------------------------------------------------------------
# Job handlers
# --------------------------------------------------------------------------
def handle_pipeline(ctx: JobContext) -> None:
    assert ctx.source_id
    source_id = ctx.source_id
    from_step = ctx.params.get("from_step") or "preflight"
    if from_step not in STEPS:
        raise errors.AppError(errors.INVALID_STEP, step=from_step)
    update_source(source_id, status=SOURCE_PROCESSING, error=None)
    base = sum(STEP_WEIGHTS[s] for s in STEPS[: STEPS.index(from_step)])
    for step in STEPS[STEPS.index(from_step):]:
        weight = STEP_WEIGHTS[step]
        src = load_source(source_id)
        state = (src.steps or {}).get(step, {}).get("state")
        if state in STEP_DONE_STATES:
            base += weight
            continue
        if step not in STEP_FUNCS:
            continue  # later phase
        ctx.check_cancelled()
        report = StepReporter(ctx, source_id, step, base, weight)
        set_step(source_id, step, state="running", progress=0.0, error=None, stage=None, notices=[])
        ctx.progress(base, step=step, force=True)
        ctx.log(f"step {step}: start")
        try:
            result = STEP_FUNCS[step](ctx, src, report)
        except JobCancelled:
            set_step(source_id, step, state="pending", progress=0.0, stage=None)
            update_source(source_id, status=SOURCE_NEW, error=errors.issue(errors.JOB_CANCELLED))
            raise
        except errors.AppError as exc:
            set_step(source_id, step, state="error", error=exc.to_dict(), stage=None, notices=report.notices)
            update_source(source_id, status=SOURCE_ERROR, error=exc.to_dict())
            raise
        except Exception as exc:
            issue = errors.issue(errors.DISK_FULL) if getattr(exc, "errno", None) == 28 else errors.issue(errors.JOB_FAILED)
            if ctx.is_cancelled():
                set_step(source_id, step, state="pending", progress=0.0, stage=None)
                update_source(source_id, status=SOURCE_NEW, error=errors.issue(errors.JOB_CANCELLED))
            else:
                set_step(source_id, step, state="error", error=issue, stage=None, notices=report.notices)
                update_source(source_id, status=SOURCE_ERROR, error=issue)
            raise
        set_step(source_id, step, state=result, progress=1.0, error=None, stage=None, notices=report.notices)
        ctx.log(f"step {step}: {result}")
        base += weight
        ctx.progress(base, step=step, force=True)
    update_source(source_id, status=SOURCE_READY, error=None)


def handle_download(ctx: JobContext) -> None:
    assert ctx.source_id
    source_id = ctx.source_id
    settings = get_settings()
    url = ctx.params["url"]
    folder = source_dir(source_id)
    update_source(source_id, status=SOURCE_DOWNLOADING, error=None)
    ctx.progress(0.0, step="download", force=True)
    try:
        result = ingest.download_url(
            url, folder, ffmpeg=settings.ffmpeg_bin,
            on_progress=lambda f: ctx.progress(0.95 * f, step="download"),
            log=ctx.log, is_cancelled=ctx.is_cancelled,
        )
    except ingest.DownloadCancelled as exc:
        update_source(source_id, status=SOURCE_ERROR, error=errors.issue(errors.JOB_CANCELLED))
        raise JobCancelled() from exc
    except Exception as exc:
        if ctx.is_cancelled():
            update_source(source_id, status=SOURCE_ERROR, error=errors.issue(errors.JOB_CANCELLED))
            raise JobCancelled() from exc
        detail = _short_error(exc)
        issue = errors.issue(errors.DOWNLOAD_FAILED, detail=detail)
        update_source(source_id, status=SOURCE_ERROR, error=issue)
        raise errors.AppError(errors.DOWNLOAD_FAILED, detail=detail) from exc
    src = load_source(source_id)
    name = result["title"] if src.name == url else src.name
    update_source(source_id, media_path=result["path"], name=name)
    ctx.progress(0.96, step="preflight", force=True)
    run_source_preflight(source_id)
    update_source(source_id, status=SOURCE_NEW)


def _short_error(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text.replace("ERROR: ", "")[:300]


def after_cancel(job: Job) -> None:
    """A job cancelled before it started never runs its handler: fix the source status here."""
    if job.status != JOB_CANCELLED or not job.source_id:
        return
    try:
        src = load_source(job.source_id)
    except errors.AppError:
        return
    if src.status == SOURCE_PROCESSING:
        update_source(src.id, status=SOURCE_NEW, error=errors.issue(errors.JOB_CANCELLED))
    elif src.status == SOURCE_DOWNLOADING:
        update_source(src.id, status=SOURCE_ERROR, error=errors.issue(errors.JOB_CANCELLED))


def register_handlers(queue: JobQueue) -> None:
    queue.register("pipeline", handle_pipeline)
    queue.register("download", handle_download)
