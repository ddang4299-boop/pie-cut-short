"""FastAPI entry point.  Run from backend/:  uvicorn app.main:app --reload"""

import logging
import platform
import re
import shutil
import sys
import time
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlmodel import col, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import clips_service, db, errors, jobs, moments_service, settings_store, tasks
from app.config import APP_VERSION, get_settings
from app.models import (
    JOB_ACTIVE,
    JOB_RUNNING,
    SOURCE_DOWNLOADING,
    SOURCE_ERROR,
    SOURCE_NEW,
    SOURCE_PROCESSING,
    SOURCE_READY,
    SOURCE_WAITING,
    CONTENT_TYPES,
    PLATFORMS,
    STEPS,
    Job,
    Moment,
    Source,
)
from pipeline import ingest, preflight, transcribe

log = logging.getLogger("shortforge")

UPLOAD_CHUNK = 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.data_path.mkdir(parents=True, exist_ok=True)
    db.init_db(settings.db_path)
    queue = jobs.JobQueue(settings.data_path)
    tasks.register_handlers(queue)
    queue.recover()  # resume jobs interrupted by a crash/close, delete temp files
    tasks.reconcile_sources()  # sources whose job gave up -> error (Retry in the Library)
    clips_service.reconcile()  # clip work left "running" by a crash -> pending
    jobs.set_queue(queue)
    watcher = moments_service.ResultWatcher()
    if settings.start_job_workers:
        queue.start()
        watcher.start()  # imports data/<id>/moments.json written by /find-moments
    try:
        yield
    finally:
        watcher.stop()
        queue.stop()
        jobs.set_queue(None)


app = FastAPI(title="ShortForge Local", version=APP_VERSION, lifespan=lifespan)

# The Vite dev server proxies /api to this app; CORS is only a fallback for direct calls.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Errors -> {"code", "severity", "params"} (translated by the frontend)
# --------------------------------------------------------------------------
@app.exception_handler(errors.AppError)
async def _app_error(_: Request, exc: errors.AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields = ", ".join(
        ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
        for err in exc.errors()
    )
    payload = errors.issue(errors.VALIDATION_ERROR, fields=fields)
    return JSONResponse(status_code=422, content=payload)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = errors.NOT_FOUND if exc.status_code == 404 else errors.INTERNAL_ERROR
    return JSONResponse(status_code=exc.status_code, content=errors.issue(code))


@app.exception_handler(Exception)
async def _unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content=errors.issue(errors.INTERNAL_ERROR))


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _cached_system_info() -> dict[str, Any]:
    s = get_settings()
    return preflight.system_info(s.ffmpeg_bin, s.ffprobe_bin, s.device)


@app.get("/health")
def health(refresh: bool = False) -> dict[str, Any]:
    """Backend status: ffmpeg version, compute device (cuda/mps/cpu) and machine issues."""
    if refresh:
        _cached_system_info.cache_clear()
    info = _cached_system_info()
    return {
        "status": "degraded" if preflight.has_errors(info["issues"]) else "ok",
        "app_version": APP_VERSION,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        **info,
    }


@app.get("/errors")
def error_codes() -> list[dict[str, Any]]:
    """The error code registry (useful for debugging translations)."""
    return [
        {"code": e.code, "severity": e.severity.value, "params": list(e.params)}
        for e in errors.REGISTRY.values()
    ]


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------
def _file_version(path: str | None) -> int | None:
    if not path:
        return None
    try:
        return int(Path(path).stat().st_mtime)
    except OSError:
        return None


def job_out(job: Job, source_name: str | None = None) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "lane": job.lane,
        "source_id": job.source_id,
        "source_name": source_name,
        "status": job.status,
        "progress": round(job.progress, 4),
        "step": job.step,
        "params": job.params,
        "error": job.error,
        "attempts": job.attempts,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def source_out(src: Source, active_job: Job | None = None, moment_counts: dict[str, int] | None = None) -> dict[str, Any]:
    thumb_v = _file_version(src.thumbnail_path)
    proxy_v = _file_version(src.proxy_path)
    has_transcript = tasks.transcript_path(src.id).exists()
    preflight_ok = bool((src.preflight or {}).get("ok"))
    return {
        "id": src.id,
        "name": src.name,
        "origin": src.origin,
        "url": src.url,
        "media_path": src.media_path,
        "owns_media": src.owns_media,
        "size_bytes": src.size_bytes,
        "duration": src.duration,
        "fps": src.fps,
        "width": src.width,
        "height": src.height,
        "has_audio": src.has_audio,
        "content_type": tasks.effective_content_type(src) if (src.content_type or src.content_type_detected) else None,
        "content_type_override": src.content_type,
        "content_type_detected": src.content_type_detected,
        "content_type_info": src.content_type_info,
        "target_platform": src.target_platform,
        "language": src.language,
        "language_override": src.language_override,
        "notes": src.notes,
        "status": src.status,
        "error": src.error,
        "preflight": src.preflight,
        "steps": {step: (src.steps or {}).get(step, {"state": "pending", "progress": 0.0}) for step in STEPS},
        "can_start": preflight_ok and active_job is None and src.status in (SOURCE_NEW, SOURCE_ERROR),
        "active_job": job_out(active_job) if active_job else None,
        "thumbnail_url": f"/sources/{src.id}/thumbnail?v={thumb_v}" if thumb_v else None,
        "proxy_url": f"/sources/{src.id}/proxy?v={proxy_v}" if proxy_v else None,
        "has_transcript": has_transcript,
        "has_analysis": tasks.analysis_path(src.id).exists(),
        "moments": moment_counts or {"approved": 0, "total": 0},
        "moments_summary": src.moments_summary,
        "moments_notes": src.moments_notes,
        "moments_provider": src.moments_provider,
        "find_moments_command": f"/find-moments {src.id}",
        "created_at": src.created_at.isoformat() if src.created_at else None,
        "updated_at": src.updated_at.isoformat() if src.updated_at else None,
    }


SOURCE_JOB_KINDS = ("pipeline", "download")  # clip jobs (editor) never make a source "busy"


def _active_jobs_by_source() -> dict[str, Job]:
    with db.session() as s:
        rows = s.exec(select(Job).where(col(Job.status).in_(JOB_ACTIVE), col(Job.kind).in_(SOURCE_JOB_KINDS))
                      .order_by(Job.created_at)).all()
    out: dict[str, Job] = {}
    for job in rows:
        if job.source_id and (job.source_id not in out or job.status == JOB_RUNNING):
            out[job.source_id] = job
    return out


def _source_response(source_id: str) -> dict[str, Any]:
    src = tasks.load_source(source_id)
    return source_out(src, _active_jobs_by_source().get(source_id), moments_service.counts(source_id).get(source_id))


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------
@app.get("/sources")
def list_sources() -> list[dict[str, Any]]:
    with db.session() as s:
        rows = s.exec(select(Source).order_by(col(Source.created_at).desc())).all()
    active = _active_jobs_by_source()
    counts = moments_service.counts()
    return [source_out(src, active.get(src.id), counts.get(src.id)) for src in rows]


@app.get("/sources/{source_id}")
def get_source(source_id: str) -> dict[str, Any]:
    return _source_response(source_id)


def _safe_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name.strip()
    return name or "video.mp4"


@app.post("/sources/upload")
async def upload_source(request: Request, name: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Raw-body upload (the file streams straight into data/<id>/, no multipart temp copy)."""
    settings = get_settings()
    name = _safe_name(name)
    if not ingest.is_video_name(name):
        raise errors.AppError(errors.UNSUPPORTED_FILE_TYPE, name=name)
    expected = int(request.headers.get("content-length") or 0)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(settings.data_path).free
    if expected and free < expected * 1.05:
        gib = 1024**3
        raise errors.AppError(errors.DISK_SPACE_LOW, needed_gb=round(expected * preflight.DISK_FACTOR / gib, 1),
                              free_gb=round(free / gib, 1))

    source_id = tasks.new_source_id()
    folder = tasks.source_dir(source_id)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / ("original" + Path(name).suffix.lower())
    part = dest.with_name(dest.name + ".part")
    written = 0
    try:
        with part.open("wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
                written += len(chunk)
        if written == 0 or (expected and written < expected):
            raise errors.AppError(errors.UPLOAD_FAILED)
        part.replace(dest)
    except errors.AppError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        if exc.errno == 28:
            raise errors.AppError(errors.DISK_FULL) from exc
        raise errors.AppError(errors.UPLOAD_FAILED) from exc
    except Exception as exc:  # client disconnected mid-upload
        shutil.rmtree(folder, ignore_errors=True)
        raise errors.AppError(errors.UPLOAD_FAILED) from exc

    with db.session() as s:
        s.add(Source(id=source_id, name=name, origin="file", media_path=str(dest), owns_media=True,
                     size_bytes=written, status=SOURCE_NEW))
        s.commit()
    await run_in_threadpool(tasks.run_source_preflight, source_id)
    return _source_response(source_id)


class ImportBody(BaseModel):
    location: str = Field(min_length=1, max_length=4000)


_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


@app.post("/sources/import")
def import_source(body: ImportBody) -> dict[str, Any]:
    """Import from a URL (downloaded with yt-dlp) or a local file path (used in place, not copied)."""
    location = body.location.strip().strip('"').strip("'").strip()
    source_id = tasks.new_source_id()
    if _URL_RE.match(location):
        with db.session() as s:
            s.add(Source(id=source_id, name=location, origin="url", url=location, status=SOURCE_DOWNLOADING))
            s.commit()
        jobs.get_queue().enqueue("download", source_id=source_id, params={"url": location}, lane="download")
        return _source_response(source_id)

    path = Path(location).expanduser()
    if not path.is_absolute():
        raise errors.AppError(errors.INVALID_LOCATION)
    if not path.is_file():
        raise errors.AppError(errors.FILE_NOT_FOUND, name=path.name or location)
    if not ingest.is_video_name(path.name):
        raise errors.AppError(errors.UNSUPPORTED_FILE_TYPE, name=path.name)
    with db.session() as s:
        s.add(Source(id=source_id, name=path.name, origin="path", media_path=str(path.resolve()),
                     owns_media=False, size_bytes=path.stat().st_size, status=SOURCE_NEW))
        s.commit()
    tasks.run_source_preflight(source_id)
    return _source_response(source_id)


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    content_type: str | None = None
    language_override: str | None = None
    notes: str | None = Field(default=None, max_length=4000)
    target_platform: str | None = None


@app.patch("/sources/{source_id}")
def patch_source(source_id: str, body: SourcePatch) -> dict[str, Any]:
    tasks.load_source(source_id)
    fields: dict[str, Any] = {}
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        fields["name"] = data["name"].strip()
    if "content_type" in data:
        if data["content_type"] not in (*CONTENT_TYPES, None):
            raise errors.AppError(errors.VALIDATION_ERROR, fields="content_type")
        fields["content_type"] = data["content_type"]
    if "language_override" in data:
        if data["language_override"] not in transcribe.LANGUAGE_OPTIONS:
            raise errors.AppError(errors.VALIDATION_ERROR, fields="language_override")
        fields["language_override"] = data["language_override"]
    if "notes" in data and data["notes"] is not None:
        fields["notes"] = data["notes"]
    if "target_platform" in data:
        if data["target_platform"] not in PLATFORMS:
            raise errors.AppError(errors.VALIDATION_ERROR, fields="target_platform")
        fields["target_platform"] = data["target_platform"]
    if fields:
        tasks.update_source(source_id, **fields)
    return _source_response(source_id)


class StartBody(BaseModel):
    language: str | None = None  # auto | vi | en | mixed
    notes: str | None = Field(default=None, max_length=4000)


def _ensure_idle(source_id: str) -> None:
    if source_id in _active_jobs_by_source():
        raise errors.AppError(errors.SOURCE_BUSY)


@app.post("/sources/{source_id}/start")
def start_source(source_id: str, body: StartBody | None = None) -> dict[str, Any]:
    """Start (or continue) the pipeline. Only runs after the user pressed Start in the Library."""
    body = body or StartBody()
    src = tasks.load_source(source_id)
    _ensure_idle(source_id)
    if not (src.preflight or {}).get("ok") or src.status == SOURCE_DOWNLOADING:
        raise errors.AppError(errors.SOURCE_NOT_STARTABLE)
    fields: dict[str, Any] = {"status": SOURCE_PROCESSING, "error": None}
    if body.language is not None:
        if body.language not in transcribe.LANGUAGE_OPTIONS:
            raise errors.AppError(errors.VALIDATION_ERROR, fields="language")
        fields["language_override"] = body.language
    if body.notes is not None:
        fields["notes"] = body.notes
    tasks.update_source(source_id, **fields)
    src = tasks.load_source(source_id)
    from_step = tasks.first_unfinished_step(src)
    if from_step is None:
        tasks.update_source(source_id, status=SOURCE_READY)
        return _source_response(source_id)
    jobs.get_queue().enqueue("pipeline", source_id=source_id, params={"from_step": from_step})
    return _source_response(source_id)


class RerunBody(BaseModel):
    from_step: str
    provider: str | None = None  # moments provider override for this run


@app.post("/sources/{source_id}/rerun")
def rerun_source(source_id: str, body: RerunBody) -> dict[str, Any]:
    src = tasks.load_source(source_id)
    if body.from_step not in tasks.IMPLEMENTED_STEPS:
        raise errors.AppError(errors.INVALID_STEP, step=body.from_step)
    _ensure_idle(source_id)
    if not src.media_path or src.status == SOURCE_DOWNLOADING:
        raise errors.AppError(errors.SOURCE_NOT_STARTABLE)
    tasks.reset_steps(source_id, body.from_step)
    tasks.update_source(source_id, status=SOURCE_PROCESSING, error=None)
    params: dict[str, Any] = {"from_step": body.from_step, "force": True}
    if body.provider:
        if body.provider not in settings_store.LLM_PROVIDERS:
            raise errors.AppError(errors.VALIDATION_ERROR, fields="provider")
        params["provider"] = body.provider
    jobs.get_queue().enqueue("pipeline", source_id=source_id, params=params)
    return _source_response(source_id)


@app.delete("/sources/{source_id}")
def delete_source(source_id: str, delete_media: bool = False) -> dict[str, Any]:
    """Delete a source. delete_media=true also removes its folder in data/ (proxy, transcript,
    uploaded/downloaded original). Files the user referenced by path are never deleted."""
    tasks.load_source(source_id)
    queue = jobs.get_queue()
    queue.cancel_for_source(source_id)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:  # wait for running jobs (pipeline or clip) to stop
        with db.session() as s:
            if not s.exec(select(Job).where(Job.source_id == source_id, Job.status == JOB_RUNNING)).first():
                break
        time.sleep(0.2)
    with db.session() as s:
        src = s.get(Source, source_id)
        if src is not None:
            s.delete(src)
        for job in s.exec(select(Job).where(Job.source_id == source_id)).all():
            s.delete(job)
        s.commit()
    moments_service.delete_for_source(source_id)
    clips_service.delete_for_source(source_id)
    removed = False
    if delete_media:
        folder = tasks.source_dir(source_id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            removed = True
    return {"id": source_id, "deleted": True, "media_deleted": removed}


@app.get("/sources/{source_id}/transcript")
def get_transcript(source_id: str) -> FileResponse:
    tasks.load_source(source_id)
    path = tasks.transcript_path(source_id)
    if not path.exists():
        raise errors.AppError(errors.TRANSCRIPT_NOT_READY)
    return FileResponse(path, media_type="application/json")


@app.get("/sources/{source_id}/proxy")
def get_proxy(source_id: str) -> FileResponse:
    src = tasks.load_source(source_id)
    if not src.proxy_path or not Path(src.proxy_path).exists():
        raise errors.AppError(errors.NOT_FOUND)
    return FileResponse(src.proxy_path, media_type="video/mp4")


@app.get("/sources/{source_id}/thumbnail")
def get_thumbnail(source_id: str) -> FileResponse:
    src = tasks.load_source(source_id)
    if not src.thumbnail_path or not Path(src.thumbnail_path).exists():
        raise errors.AppError(errors.NOT_FOUND)
    return FileResponse(src.thumbnail_path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.get("/sources/{source_id}/analysis")
def get_analysis(source_id: str) -> FileResponse:
    tasks.load_source(source_id)
    path = tasks.analysis_path(source_id)
    if not path.exists():
        raise errors.AppError(errors.ANALYSIS_NOT_READY)
    return FileResponse(path, media_type="application/json")


@app.get("/sources/{source_id}/waveform")
def get_waveform(source_id: str) -> FileResponse:
    tasks.load_source(source_id)
    path = tasks.waveform_path(source_id)
    if not path.exists():
        raise errors.AppError(errors.ANALYSIS_NOT_READY)
    return FileResponse(path, media_type="application/json")


# --------------------------------------------------------------------------
# Moments
# --------------------------------------------------------------------------
@app.get("/sources/{source_id}/moments")
def get_moments(source_id: str) -> list[dict[str, Any]]:
    tasks.load_source(source_id)
    return moments_service.list_moments(source_id)


class MomentCreate(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str = Field(default="", max_length=300)
    hook: str = Field(default="", max_length=300)
    snap: bool = True
    status: str = "approved"


@app.post("/sources/{source_id}/moments")
def create_moment(source_id: str, body: MomentCreate) -> dict[str, Any]:
    moment = moments_service.create_manual(source_id, start=body.start, end=body.end, title=body.title,
                                           hook=body.hook, snap_bounds=body.snap, status=body.status)
    if moment["status"] == "approved":
        clips_service.ensure_clip(moment)
    return moment


class FindBody(BaseModel):
    provider: str | None = None


@app.post("/sources/{source_id}/moments/find")
def find_moments(source_id: str, body: FindBody | None = None) -> dict[str, Any]:
    """(Re)propose moments: re-runs the moments step (claude_code writes a new request file)."""
    return rerun_source(source_id, RerunBody(from_step="moments", provider=(body or FindBody()).provider))


@app.post("/sources/{source_id}/moments/import")
def import_moments_now(source_id: str) -> dict[str, Any]:
    """Check data/<id>/moments.json now instead of waiting for the watcher (also re-imports)."""
    tasks.load_source(source_id)
    result = moments_service.check_result_file(source_id, force=True)
    if result == "missing":
        raise errors.AppError(errors.FILE_NOT_FOUND, name="moments.json")
    if result == "invalid":
        issue = tasks.load_source(source_id).steps.get("moments", {}).get("error") or {}
        raise errors.AppError(errors.MOMENTS_INVALID, detail=(issue.get("params") or {}).get("detail", ""))
    if tasks.load_source(source_id).status != SOURCE_WAITING:
        tasks.update_source(source_id, status=SOURCE_READY, error=None)
    return _source_response(source_id)


class MomentPatch(BaseModel):
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, gt=0)
    snap: bool = True  # False when the user held Alt (or for undo/redo, which restores exact values)
    status: str | None = None
    title: str | None = Field(default=None, max_length=300)
    hook: str | None = Field(default=None, max_length=300)
    reason: str | None = Field(default=None, max_length=1000)
    caption_style: str | None = None
    platform: str | None = None


@app.patch("/moments/{moment_id}")
def patch_moment(moment_id: int, body: MomentPatch) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True, exclude={"snap"})
    data = {k: v for k, v in data.items() if v is not None}
    moment = moments_service.update_moment(moment_id, data, snap_bounds=body.snap)
    if moment["status"] == "approved":
        clips_service.ensure_clip(moment)  # approving creates the clip (once)
    clips_service.sync_moment(moment)  # an existing clip follows range / style / platform changes
    return moment


@app.delete("/moments/{moment_id}")
def remove_moment(moment_id: int) -> dict[str, Any]:
    moments_service.delete_moment(moment_id)
    clips_service.delete_for_moment(moment_id)
    return {"id": moment_id, "deleted": True}


@app.post("/moments/{moment_id}/clip")
def open_moment_clip(moment_id: int) -> dict[str, Any]:
    """The clip of a moment, created on demand (the editor opens moments by id)."""
    with db.session() as s:
        m = s.get(Moment, moment_id)
    if m is None:
        raise errors.AppError(errors.MOMENT_NOT_FOUND)
    return clips_service.ensure_clip(moments_service.moment_out(m))


# --------------------------------------------------------------------------
# Clips (EDL)
# --------------------------------------------------------------------------
@app.get("/sources/{source_id}/clips")
def get_clips(source_id: str) -> list[dict[str, Any]]:
    tasks.load_source(source_id)
    return clips_service.list_clips(source_id)


@app.get("/clips/{clip_id}")
def get_clip(clip_id: str) -> dict[str, Any]:
    return clips_service.get_clip(clip_id)


class ClipPatch(BaseModel):
    edl: dict[str, Any] | None = None  # partial EDL: each section sent replaces that section
    status: str | None = None


@app.patch("/clips/{clip_id}")
def patch_clip(clip_id: str, body: ClipPatch) -> dict[str, Any]:
    return clips_service.patch_clip(clip_id, edl_patch=body.edl, status=body.status)


class ReframeBody(BaseModel):
    mode: str | None = None  # auto | split | cursor; None = keep the clip's mode


@app.post("/clips/{clip_id}/reframe")
def reframe_clip(clip_id: str, body: ReframeBody | None = None) -> dict[str, Any]:
    """Recompute the framing automatically (overwrites manual keyframes; the UI confirms first)."""
    return clips_service.recompute(clip_id, (body or ReframeBody()).mode)


class SnapBody(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)


@app.post("/sources/{source_id}/snap")
def snap_range(source_id: str, body: SnapBody) -> dict[str, float]:
    """Preview where a range would snap (no DB change)."""
    src = tasks.load_source(source_id)
    start, end = moments_service._snap(source_id, body.start, body.end, src.duration)
    return {"start": start, "end": end}


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
@app.get("/settings")
def get_app_settings() -> dict[str, Any]:
    s = get_settings()
    return {
        **settings_store.get_all(),
        "env": {
            "llm_provider": s.llm_provider,
            "anthropic_configured": bool(s.anthropic_api_key),
            "openai_configured": bool(s.openai_api_key),
            "ollama_host": s.ollama_host,
            "ollama_model": s.ollama_model,
        },
        "effective_llm_provider": tasks.selected_provider(),
    }


@app.patch("/settings")
def patch_app_settings(body: dict[str, Any]) -> dict[str, Any]:
    body.pop("env", None)
    body.pop("effective_llm_provider", None)
    settings_store.update(body)
    if "rubric_weights" in body:
        moments_service.rescore_all()
    return get_app_settings()


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------
@app.get("/jobs")
def list_jobs(source_id: str | None = None, active: bool = False, limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    with db.session() as s:
        q = select(Job)
        if source_id:
            q = q.where(Job.source_id == source_id)
        if active:
            q = q.where(col(Job.status).in_(JOB_ACTIVE))
        rows = s.exec(q.order_by(col(Job.created_at).desc(), col(Job.id).desc()).limit(limit)).all()
        names = {src.id: src.name for src in s.exec(select(Source)).all()}
    return [job_out(j, names.get(j.source_id or "")) for j in rows]


def _load_job(job_id: int) -> Job:
    with db.session() as s:
        job = s.get(Job, job_id)
    if job is None:
        raise errors.AppError(errors.JOB_NOT_FOUND)
    return job


@app.get("/jobs/{job_id}")
def get_job(job_id: int) -> dict[str, Any]:
    return job_out(_load_job(job_id))


@app.get("/jobs/{job_id}/log")
def get_job_log(job_id: int) -> PlainTextResponse:
    _load_job(job_id)
    return PlainTextResponse(jobs.get_queue().read_log(job_id))


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> dict[str, Any]:
    _load_job(job_id)
    job = jobs.get_queue().cancel(job_id)
    tasks.after_cancel(job)
    return job_out(job)


if sys.platform == "win32":
    # Make Windows consoles print UTF-8 logs (Vietnamese file names).
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
