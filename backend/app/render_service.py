"""Render + export of clips: the background "render" and "capcut" jobs, the export list, CapCut
round-trip import.

State lives in `clip.work`:
    work["render"] = {state, job_id, renderer, error, info (render.json), out_dir, queued_at, finished_at}
    work["capcut"] = {state, job_id, error, info, out_dir}
where state is idle | queued | running | done | error | cancelled. While a job is active, its
progress/step are read from the Job row.

Outputs go to <output folder>/<source_id>/<clip_id>/ (Settings "output_dir", default data/output):
clip_final.mp4, captions.srt, thumb.jpg, render.json, capcut/ + capcut_bundle.zip, and
clip_capcut.mp4 after an import from CapCut. The output folder is remembered per clip, so changing
the setting later does not lose earlier renders.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlmodel import col, select

from app import clips_service, db, errors, jobs, settings_store
from app.config import get_settings
from app.models import JOB_ACTIVE, Clip, Job, utcnow
from pipeline import captions as captions_mod, export_capcut, presets, render
from pipeline.keyframes import clip_duration

RENDER_JOB = "render"
CAPCUT_JOB = "capcut"
RENDER_LANE = "render"
TASKS = ("render", "capcut")
ACTIVE = ("queued", "running")
FINAL_NAME = "clip_final.mp4"
IMPORTED_NAME = "clip_capcut.mp4"
BUNDLE_ZIP = "capcut_bundle.zip"


# --------------------------------------------------------------------------
# paths + state
# --------------------------------------------------------------------------
def output_root() -> Path:
    custom = str(settings_store.get("output_dir") or "").strip()
    if custom and Path(custom).is_absolute():
        return Path(custom)
    return get_settings().data_path / "output"


def out_dir(clip: Clip) -> Path:
    stored = ((clip.work or {}).get("render") or {}).get("out_dir")
    return Path(stored) if stored else output_root() / clip.source_id / clip.id


def work_dir(clip: Clip) -> Path:
    return clips_service.clip_dir(clip) / "render_work"


def _task(clip: Clip, task: str) -> dict[str, Any]:
    return {"state": "idle", "job_id": None, "error": None, "info": None, **((clip.work or {}).get(task) or {})}


def _set_task(clip_id: str, task: str, **changes: Any) -> Clip:
    clip = clips_service._load(clip_id)
    work = dict(clip.work or {})
    work[task] = {**_task(clip, task), **changes}
    clip.work = work
    return clips_service._save(clip)


def _job(job_id: int | None) -> Job | None:
    if not job_id:
        return None
    with db.session() as s:
        return s.get(Job, job_id)


def task_out(clip: Clip, task: str) -> dict[str, Any]:
    """The task's state with live progress; a job that ended without updating the clip (cancelled
    before it started, or a crash) is reported as such."""
    t = _task(clip, task)
    out = {"state": t["state"], "error": t.get("error"), "info": t.get("info"), "job_id": t.get("job_id"),
           "renderer": t.get("renderer"), "progress": 1.0 if t["state"] == "done" else 0.0, "step": None,
           "started_at": None, "finished_at": t.get("finished_at")}
    if t["state"] in ACTIVE:
        job = _job(t.get("job_id"))
        if job is None or job.status not in JOB_ACTIVE:
            out["state"] = "cancelled" if job is None or job.status == "cancelled" else "error"
            out["error"] = (job.error if job else None) or errors.issue(errors.JOB_CANCELLED)
        else:
            out["state"] = "running" if job.status == "running" else "queued"
            out["progress"] = round(job.progress, 4)
            out["step"] = job.step
            out["started_at"] = job.started_at.isoformat() if job.started_at else None
    return out


def _file_info(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    st = path.stat()
    return {"name": path.name, "size": st.st_size, "mtime": int(st.st_mtime)}


def export_out(clip: Clip) -> dict[str, Any]:
    base = clips_service.clip_out(clip, full=False)
    folder = out_dir(clip)
    edl = clip.edl or {}
    preset = presets.resolve(edl.get("platform") or "youtube_shorts", settings_store.get("platform_presets"))
    base.update(
        render=task_out(clip, "render"), capcut=task_out(clip, "capcut"), out_dir=str(folder),
        files={"final": _file_info(folder / FINAL_NAME), "thumb": _file_info(folder / "thumb.jpg"),
               "srt": _file_info(folder / "captions.srt"), "bundle": _file_info(folder / BUNDLE_ZIP),
               "imported": _file_info(folder / IMPORTED_NAME)},
        length_warning=presets.length_warning(clip_duration(edl.get("segments") or []), preset),
    )
    return base


def list_exports(source_id: str | None = None) -> list[dict[str, Any]]:
    """Clips that are approved, rendered, or have a render task (the Export page)."""
    with db.session() as s:
        q = select(Clip).order_by(col(Clip.created_at))
        if source_id:
            q = q.where(Clip.source_id == source_id)
        rows = s.exec(q).all()
    out = []
    for clip in rows:
        if clip.status in ("approved", "rendered") or _task(clip, "render")["state"] != "idle":
            out.append(export_out(clip))
    return out


# --------------------------------------------------------------------------
# queueing
# --------------------------------------------------------------------------
def _busy(clip: Clip) -> bool:
    return any(task_out(clip, t)["state"] in ACTIVE for t in TASKS)


def queue_render(clip_id: str, renderer: str | None = None) -> dict[str, Any]:
    """Queue a render. Clicking Render is the approval of the edit (draft/review -> approved)."""
    clip = clips_service._load(clip_id)
    if _busy(clip):
        raise errors.AppError(errors.RENDER_BUSY)
    if clips_service.clip_out(clip)["busy"]:
        raise errors.AppError(errors.CLIP_BUSY)
    renderer = renderer if renderer in settings_store.RENDERERS else settings_store.get("renderer")
    if clip.status in ("draft", "review"):
        clips_service.patch_clip(clip_id, status="approved")
    job = jobs.get_queue().enqueue(RENDER_JOB, source_id=clip.source_id, params={"clip_id": clip_id, "renderer": renderer},
                                   lane=RENDER_LANE)
    clip = _set_task(clip_id, "render", state="queued", job_id=job.id, renderer=renderer, error=None,
                     out_dir=str(out_dir(clip)), queued_at=utcnow().isoformat())
    return export_out(clip)


def queue_batch(clip_ids: list[str], renderer: str | None = None) -> dict[str, Any]:
    queued, skipped = [], []
    for cid in clip_ids:
        try:
            queue_render(cid, renderer)
            queued.append(cid)
        except errors.AppError as exc:
            skipped.append({"clip_id": cid, **exc.to_dict()})
    return {"queued": queued, "skipped": skipped}


def queue_capcut(clip_id: str) -> dict[str, Any]:
    clip = clips_service._load(clip_id)
    if _busy(clip):
        raise errors.AppError(errors.RENDER_BUSY)
    if not (out_dir(clip) / FINAL_NAME).exists():
        raise errors.AppError(errors.RENDER_NOT_FOUND)
    job = jobs.get_queue().enqueue(CAPCUT_JOB, source_id=clip.source_id, params={"clip_id": clip_id}, lane=RENDER_LANE)
    return export_out(_set_task(clip_id, "capcut", state="queued", job_id=job.id, error=None))


def cancel(clip_id: str) -> dict[str, Any]:
    clip = clips_service._load(clip_id)
    for t in TASKS:
        st = task_out(clip, t)
        if st["state"] in ACTIVE and st["job_id"]:
            jobs.get_queue().cancel(st["job_id"])
    return export_out(clips_service._load(clip_id))


# --------------------------------------------------------------------------
# render input
# --------------------------------------------------------------------------
def _language(src: Any) -> str:
    lang = src.language_override if src.language_override not in (None, "", "auto") else src.language
    return lang or "en"


def build_input(clip: Clip, renderer: str, hooks: render.Hooks, *, on_progress: Any = None) -> render.RenderInput:
    """RenderInput for a clip: a frame-accurate intermediate of the clip's source range, plus the
    Voice Isolator output and voiceover from the voice cache when enabled."""
    settings = get_settings()
    src = clips_service._source(clip.source_id)
    edl = clips_service.seed_captions(clip).edl or {}
    media = Path(src.media_path or "")
    if not media.exists():
        media = Path(src.proxy_path or "")
    if not media.exists():
        raise errors.AppError(errors.FILE_NOT_FOUND, name=Path(src.media_path or "").name or src.name)
    preset = presets.resolve(edl.get("platform") or "youtube_shorts", settings_store.get("platform_presets"))
    fps = render.output_fps(edl, preset)
    segs = edl.get("segments") or []
    wd = work_dir(clip)
    wd.mkdir(parents=True, exist_ok=True)
    rsrc, lo = render.render_source(media, wd, start=min(s["start"] for s in segs), end=max(s["end"] for s in segs),
                                    fps=fps, has_audio=bool(src.has_audio), ffmpeg=settings.ffmpeg_bin, log=hooks.log,
                                    on_process=hooks.on_process, on_progress=on_progress)
    audio = edl.get("audio") or {}
    isolated = None
    if audio.get("isolate_voice") and audio.get("isolated_path"):
        p = settings.data_path / audio["isolated_path"]
        if p.exists():
            isolated = (p, float(audio.get("isolated_start", 0.0)))
    vo = edl.get("voiceover") or {}
    voiceover, vo_duration = None, None
    if vo.get("enabled") and vo.get("audio_path"):
        p = settings.data_path / vo["audio_path"]
        if p.exists():
            voiceover, vo_duration = p, vo.get("duration") or _probe_duration(p, settings.ffprobe_bin)
    return render.RenderInput(
        edl=edl, media=rsrc, media_offset=lo, src_w=int(src.width or 1920), src_h=int(src.height or 1080),
        has_audio=bool(src.has_audio), language=_language(src), preset=preset, work_dir=wd, out_dir=out_dir(clip),
        renderer=renderer, isolated=isolated, voiceover=voiceover, vo_duration=vo_duration,
        ffmpeg=settings.ffmpeg_bin, ffprobe=settings.ffprobe_bin, data_dir=settings.data_path)


def _probe_duration(path: Path, ffprobe: str) -> float | None:
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _hooks(ctx: jobs.JobContext, base: float = 0.0, span: float = 1.0) -> render.Hooks:
    return render.Hooks(progress=lambda f, step: ctx.progress(base + span * f, step=step), log=ctx.log,
                        on_process=ctx.attach_process, cancelled=ctx.is_cancelled)


def _cleanup_parts(folder: Path) -> None:
    if folder.exists():
        for p in folder.glob("*.part"):
            p.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------
SOURCE_SHARE = 0.05  # progress share of building the intermediate


def handle_render(ctx: jobs.JobContext) -> None:
    from app import tasks

    clip_id = ctx.params["clip_id"]
    renderer = ctx.params.get("renderer") or settings_store.get("renderer")
    clip = _set_task(clip_id, "render", state="running", job_id=ctx.job_id, renderer=renderer, error=None)
    folder = out_dir(clip)
    ctx.progress(0.0, step="source", force=True)
    try:
        hooks = _hooks(ctx, SOURCE_SHARE, 1 - SOURCE_SHARE)
        inp = build_input(clip, renderer, hooks, on_progress=lambda f: ctx.progress(SOURCE_SHARE * f, step="source"))
        ctx.check_cancelled()
        ctx.log(f"render {clip_id}: renderer={renderer} media={inp.media.name} offset={inp.media_offset} "
                f"preset={inp.preset['platform']} out={folder}")
        result = render.render_clip(inp, hooks)
        info = result.to_dict()
        warn = presets.length_warning(result.duration, inp.preset)
        if warn:
            info["warnings"].append({"code": "CLIP_TOO_LONG", "params": warn})
        (folder / "captions.srt").write_text(captions_mod.edl_srt(inp.edl), encoding="utf-8")
        info["rendered_at"] = utcnow().isoformat()
        (folder / "render.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        for w in info["warnings"]:
            ctx.log(f"warning: {w}")
        _set_task(clip_id, "render", state="done", info=info, error=None, finished_at=info["rendered_at"])
        clips_service.patch_clip(clip_id, status="rendered")
    except (render.Cancelled, jobs.JobCancelled):
        _set_task(clip_id, "render", state="cancelled", finished_at=utcnow().isoformat())
        _cleanup_parts(folder)
        raise jobs.JobCancelled() from None
    except errors.AppError as exc:
        _set_task(clip_id, "render", state="error", error=exc.to_dict(), finished_at=utcnow().isoformat())
        _cleanup_parts(folder)
        raise
    except Exception as exc:
        if ctx.is_cancelled():
            _set_task(clip_id, "render", state="cancelled", finished_at=utcnow().isoformat())
            _cleanup_parts(folder)
            raise jobs.JobCancelled() from None
        detail = exc.detail[-300:] if isinstance(exc, render.RenderError) and exc.detail else tasks._short_error(exc)
        ctx.log(f"render failed: {exc!r}\n{getattr(exc, 'detail', '')}")
        issue = errors.issue(errors.RENDER_FAILED, detail=detail)
        _set_task(clip_id, "render", state="error", error=issue, finished_at=utcnow().isoformat())
        _cleanup_parts(folder)
        raise errors.AppError(errors.RENDER_FAILED, detail=detail) from exc
    finally:
        shutil.rmtree(work_dir(clip), ignore_errors=True)  # intermediates (no temp files left behind)


def handle_capcut(ctx: jobs.JobContext) -> None:
    from app import tasks

    clip_id = ctx.params["clip_id"]
    clip = _set_task(clip_id, "capcut", state="running", job_id=ctx.job_id, error=None)
    folder = out_dir(clip)
    try:
        hooks = _hooks(ctx, 0.1, 0.9)
        renderer = _task(clip, "render").get("renderer") or "ffmpeg"
        inp = build_input(clip, renderer, hooks, on_progress=lambda f: ctx.progress(0.1 * f, step="source"))
        info = export_capcut.build_bundle(inp, folder / "capcut", final=folder / FINAL_NAME,
                                          srt=captions_mod.edl_srt(inp.edl), zip_path=folder / BUNDLE_ZIP, hooks=hooks,
                                          title=clips_service.clip_out(clip, full=False)["title"])
        _set_task(clip_id, "capcut", state="done", info=info, error=None, finished_at=utcnow().isoformat())
    except (render.Cancelled, jobs.JobCancelled):
        _set_task(clip_id, "capcut", state="cancelled")
        _cleanup_parts(folder / "capcut")
        _cleanup_parts(folder)
        raise jobs.JobCancelled() from None
    except errors.AppError as exc:
        _set_task(clip_id, "capcut", state="error", error=exc.to_dict())
        raise
    except Exception as exc:
        if ctx.is_cancelled():
            _set_task(clip_id, "capcut", state="cancelled")
            raise jobs.JobCancelled() from None
        detail = exc.detail[-300:] if isinstance(exc, render.RenderError) and exc.detail else tasks._short_error(exc)
        ctx.log(f"capcut bundle failed: {exc!r}\n{getattr(exc, 'detail', '')}")
        _set_task(clip_id, "capcut", state="error", error=errors.issue(errors.EXPORT_FAILED, detail=detail))
        raise errors.AppError(errors.EXPORT_FAILED, detail=detail) from exc
    finally:
        shutil.rmtree(work_dir(clip), ignore_errors=True)


def register(queue: jobs.JobQueue) -> None:
    queue.register(RENDER_JOB, handle_render)
    queue.register(CAPCUT_JOB, handle_capcut)


def reconcile() -> int:
    """At startup: render/capcut state left queued/running without an active job -> cancelled."""
    fixed = 0
    with db.session() as s:
        active = {j.id for j in s.exec(select(Job).where(col(Job.kind).in_((RENDER_JOB, CAPCUT_JOB)),
                                                         col(Job.status).in_(JOB_ACTIVE))).all()}
        for clip in s.exec(select(Clip)).all():
            work = dict(clip.work or {})
            changed = False
            for t in TASKS:
                st = work.get(t) or {}
                if st.get("state") in ACTIVE and st.get("job_id") not in active:
                    work[t] = {**st, "state": "cancelled"}
                    changed = True
            if changed or (work_dir(clip).exists() and not any(
                    (work.get(t) or {}).get("job_id") in active for t in TASKS)):
                # a crash mid-render: drop the intermediates and half-written outputs
                shutil.rmtree(work_dir(clip), ignore_errors=True)
                _cleanup_parts(out_dir(clip))
                _cleanup_parts(out_dir(clip) / "capcut")
            if changed:
                clip.work = work
                s.add(clip)
                fixed += 1
        s.commit()
    return fixed


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------
FILE_KINDS = {"final": (FINAL_NAME, "video/mp4"), "thumb": ("thumb.jpg", "image/jpeg"),
              "srt": ("captions.srt", "application/x-subrip"), "bundle": (BUNDLE_ZIP, "application/zip"),
              "imported": (IMPORTED_NAME, "video/mp4")}


def file_path(clip_id: str, kind: str) -> tuple[Path, str]:
    if kind not in FILE_KINDS:
        raise errors.AppError(errors.VALIDATION_ERROR, fields="kind")
    name, media_type = FILE_KINDS[kind]
    path = out_dir(clips_service._load(clip_id)) / name
    if not path.exists():
        raise errors.AppError(errors.RENDER_NOT_FOUND)
    return path, media_type


def download_name(clip_id: str, kind: str) -> str:
    clip = clips_service._load(clip_id)
    title = clips_service.clip_out(clip, full=False)["title"] or clip.id
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()[:60] or clip.id
    name, _ = FILE_KINDS[kind]
    return f"{safe}{Path(name).suffix}" if kind in ("final", "imported") else f"{safe}_{name}"


def open_folder(clip_id: str) -> dict[str, Any]:
    folder = out_dir(clips_service._load(clip_id))
    if not folder.exists():
        raise errors.AppError(errors.RENDER_NOT_FOUND)
    try:
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except OSError as exc:
        raise errors.AppError(errors.OPEN_FOLDER_FAILED) from exc
    return {"ok": True, "path": str(folder)}


def import_edited(clip_id: str, upload: Path, name: str) -> dict[str, Any]:
    """An MP4 edited in CapCut becomes the clip's final version (clip_capcut.mp4)."""
    settings = get_settings()
    clip = clips_service._load(clip_id)
    folder = out_dir(clip)
    try:
        probe = _probe_video(upload, settings.ffprobe_bin)
        if not probe:
            raise errors.AppError(errors.IMPORT_INVALID_FILE, name=name)
        folder.mkdir(parents=True, exist_ok=True)
        os.replace(upload, folder / IMPORTED_NAME)
    finally:
        upload.unlink(missing_ok=True)
    work = dict(clip.work or {})
    work["imported"] = {"name": name, "at": utcnow().isoformat(), **probe}
    clip.work = work
    clips_service._save(clip)
    return export_out(clips_service._load(clip_id))


def _probe_video(path: Path, ffprobe: str) -> dict[str, Any] | None:
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height:format=duration", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(r.stdout or "{}")
        stream = (data.get("streams") or [None])[0]
        duration = float((data.get("format") or {}).get("duration") or 0)
        if r.returncode != 0 or not stream or duration <= 0:
            return None
        return {"width": int(stream["width"]), "height": int(stream["height"]), "duration": round(duration, 3)}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None


def delete_outputs(clip: Clip) -> None:
    shutil.rmtree(out_dir(clip), ignore_errors=True)
