"""Clips: created when a moment is approved; EDL edits; the background "clip" job
(rough cut + automatic reframing).

- `ensure_clip(moment)` creates the clip of an approved moment (once) and queues its job.
- A clip with a single segment follows later range changes of its moment; framing is recomputed
  unless the user already edited it.
- `patch_clip` merges a partial EDL, validates it and stores it. Editing `crop` marks the framing
  as the user's, so a running or later automatic job never overwrites it silently.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from sqlmodel import col, select

from app import db, edl as edl_mod, errors, jobs, settings_store
from app.models import Clip, Moment, Source, utcnow
from pipeline import reframe

CLIP_JOB = "clip"
CLIP_LANE = "edit"
WORK_STEPS = ("prepare", "reframe")


def new_clip_id() -> str:
    return "clip_" + secrets.token_hex(4)


def clip_dir(clip: Clip) -> Path:
    from app import tasks

    return tasks.source_dir(clip.source_id) / "clips" / clip.id


def _empty_work() -> dict[str, Any]:
    return {step: {"state": "pending", "error": None, "info": None} for step in WORK_STEPS} | {
        "crop_edits": 0, "crop_user_edited": False}


def _load(clip_id: str) -> Clip:
    with db.session() as s:
        clip = s.get(Clip, clip_id)
    if clip is None:
        raise errors.AppError(errors.CLIP_NOT_FOUND)
    return clip


def _save(clip: Clip) -> Clip:
    clip.updated_at = utcnow()
    with db.session() as s:
        s.add(clip)
        s.commit()
        s.refresh(clip)
    return clip


def _set_work(clip_id: str, step: str, **changes: Any) -> Clip:
    clip = _load(clip_id)
    work = {**_empty_work(), **(clip.work or {})}
    work[step] = {**work.get(step, {}), **changes}
    clip.work = work
    return _save(clip)


def clip_out(clip: Clip, *, full: bool = True) -> dict[str, Any]:
    from app import moments_service

    edl = dict(clip.edl or {})
    edl["status"] = clip.status
    work = {**_empty_work(), **(clip.work or {})}
    busy = any(work[s]["state"] in ("queued", "running") for s in WORK_STEPS)
    out: dict[str, Any] = {
        "id": clip.id, "source_id": clip.source_id, "moment_id": clip.moment_id, "status": clip.status,
        "duration": round(sum(s["end"] - s["start"] for s in edl.get("segments") or []), 3),
        "work": work, "busy": busy,
        "created_at": clip.created_at.isoformat() if clip.created_at else None,
        "updated_at": clip.updated_at.isoformat() if clip.updated_at else None,
    }
    with db.session() as s:
        m = s.get(Moment, clip.moment_id) if clip.moment_id is not None else None
    out["moment"] = moments_service.moment_out(m) if m else None
    out["title"] = (m.title if m else "") or ""
    if full:
        out["edl"] = edl
    else:
        out["crop_mode"] = (edl.get("crop") or {}).get("mode")
        out["platform"] = edl.get("platform")
    return out


def get_clip(clip_id: str) -> dict[str, Any]:
    return clip_out(_load(clip_id))


def list_clips(source_id: str) -> list[dict[str, Any]]:
    with db.session() as s:
        rows = s.exec(select(Clip).where(Clip.source_id == source_id).order_by(col(Clip.created_at))).all()
        moment_ids = {m.id for m in s.exec(select(Moment).where(Moment.source_id == source_id)).all()}
    out = []
    for clip in rows:
        if clip.moment_id is not None and clip.moment_id not in moment_ids:
            delete_clip(clip.id)  # its moment was replaced by a new proposal run
            continue
        out.append(clip_out(clip, full=False))
    return sorted(out, key=lambda c: (c["moment"] or {}).get("start", 0.0))


def clip_for_moment(moment_id: int) -> Clip | None:
    with db.session() as s:
        return s.exec(select(Clip).where(Clip.moment_id == moment_id)).first()


def _source(source_id: str) -> Source:
    from app import tasks

    return tasks.load_source(source_id)


def ensure_clip(moment: dict[str, Any], *, enqueue: bool = True) -> dict[str, Any]:
    """The clip of a moment, created (and its job queued) on first call."""
    from app import tasks

    existing = clip_for_moment(int(moment["id"]))
    if existing is not None:
        return clip_out(existing)
    src = _source(moment["source_id"])
    clip_id = new_clip_id()
    mode = reframe.default_mode(tasks.effective_content_type(src))
    data = edl_mod.build_default(clip_id=clip_id, source_id=src.id, moment=moment, mode=mode,
                                 default_style=settings_store.get("default_caption_style"))
    clip = Clip(id=clip_id, source_id=src.id, moment_id=int(moment["id"]), edl=data, work=_empty_work())
    with db.session() as s:
        s.add(clip)
        s.commit()
        s.refresh(clip)
    if enqueue:
        queue_work(clip.id)
    return clip_out(_load(clip.id))


def queue_work(clip_id: str, *, steps: tuple[str, ...] = WORK_STEPS, mode: str | None = None) -> None:
    clip = _load(clip_id)
    work = {**_empty_work(), **(clip.work or {})}
    if any(work[s]["state"] in ("queued", "running") for s in WORK_STEPS):
        raise errors.AppError(errors.CLIP_BUSY)
    for step in steps:
        work[step] = {"state": "queued", "error": None, "info": work.get(step, {}).get("info")}
    clip.work = work
    _save(clip)
    params: dict[str, Any] = {"clip_id": clip_id, "steps": list(steps)}
    if mode:
        params["mode"] = mode
    jobs.get_queue().enqueue(CLIP_JOB, source_id=clip.source_id, params=params, lane=CLIP_LANE)


def patch_clip(clip_id: str, *, edl_patch: dict[str, Any] | None = None, status: str | None = None) -> dict[str, Any]:
    clip = _load(clip_id)
    if status is not None:
        if status not in edl_mod.CLIP_STATUSES:
            raise errors.AppError(errors.VALIDATION_ERROR, fields="status")
        clip.status = status
    if edl_patch:
        src = _source(clip.source_id)
        merged = edl_mod.merge(clip.edl or {}, edl_patch)
        merged["status"] = clip.status
        clip.edl = edl_mod.validate(merged, source_duration=src.duration)
        if "crop" in edl_patch:
            work = {**_empty_work(), **(clip.work or {})}
            work["crop_edits"] = int(work.get("crop_edits", 0)) + 1
            work["crop_user_edited"] = True
            clip.work = work
    else:
        clip.edl = {**(clip.edl or {}), "status": clip.status}
    return clip_out(_save(clip))


def recompute(clip_id: str, mode: str | None = None) -> dict[str, Any]:
    if mode is not None and mode not in edl_mod.CROP_MODES:
        raise errors.AppError(errors.VALIDATION_ERROR, fields="mode")
    queue_work(clip_id, steps=WORK_STEPS, mode=mode)
    return get_clip(clip_id)


def sync_moment(moment: dict[str, Any]) -> None:
    """After a moment changed: a single-segment clip follows its range; title/hook/style follow too
    while the user has not touched them in the editor."""
    clip = clip_for_moment(int(moment["id"]))
    if clip is None:
        return
    data = dict(clip.edl or {})
    segments = data.get("segments") or []
    changed_range = False
    if len(segments) == 1 and (abs(segments[0]["start"] - moment["start"]) > 1e-3 or
                               abs(segments[0]["end"] - moment["end"]) > 1e-3):
        data["segments"] = [{"start": moment["start"], "end": moment["end"]}]
        changed_range = True
    if moment.get("caption_style"):
        data["captions"] = {**(data.get("captions") or {}), "style": moment["caption_style"]}
    if moment.get("platform"):
        data["platform"] = moment["platform"]
    src = _source(clip.source_id)
    clip.edl = edl_mod.validate(data, source_duration=src.duration)
    _save(clip)
    if changed_range:
        work = {**_empty_work(), **(clip.work or {})}
        busy = any(work[s]["state"] in ("queued", "running") for s in WORK_STEPS)
        steps = ("prepare",) if work.get("crop_user_edited") else WORK_STEPS
        if not busy:
            queue_work(clip.id, steps=steps)


def delete_clip(clip_id: str) -> None:
    import shutil

    with db.session() as s:
        clip = s.get(Clip, clip_id)
        if clip is None:
            return
        folder = clip_dir(clip)
        s.delete(clip)
        s.commit()
    shutil.rmtree(folder, ignore_errors=True)


def delete_for_moment(moment_id: int) -> None:
    clip = clip_for_moment(moment_id)
    if clip is not None:
        delete_clip(clip.id)


def delete_for_source(source_id: str) -> None:
    with db.session() as s:
        for clip in s.exec(select(Clip).where(Clip.source_id == source_id)).all():
            s.delete(clip)
        s.commit()


# --------------------------------------------------------------------------
# job
# --------------------------------------------------------------------------
def handle_clip(ctx: jobs.JobContext) -> None:
    from app import tasks
    from app.config import get_settings
    from pipeline import prepare

    clip_id = ctx.params["clip_id"]
    steps = [s for s in ctx.params.get("steps") or WORK_STEPS if s in WORK_STEPS]
    settings = get_settings()
    clip = _load(clip_id)
    src = _source(clip.source_id)
    segments = (clip.edl or {}).get("segments") or []
    start_edits = int((clip.work or {}).get("crop_edits", 0))
    weights = {"prepare": 0.25, "reframe": 0.75} if len(steps) == 2 else {steps[0]: 1.0} if steps else {}
    base = 0.0
    for step in steps:
        ctx.check_cancelled()
        _set_work(clip_id, step, state="running", error=None)
        ctx.progress(base, step=step, force=True)
        w = weights[step]
        try:
            if step == "prepare":
                media = Path(src.media_path or "")
                if not media.exists():
                    raise errors.AppError(errors.FILE_NOT_FOUND, name=media.name or "?")
                meta = prepare.rough_cut(
                    media, clip_dir(clip), start=min(s["start"] for s in segments),
                    end=max(s["end"] for s in segments), duration=src.duration, ffmpeg=settings.ffmpeg_bin,
                    ffprobe=settings.ffprobe_bin, log=ctx.log, on_process=ctx.attach_process)
                _set_work(clip_id, step, state="done", info={"source_start": meta["source_start"],
                                                             "requested_start": meta["requested_start"],
                                                             "requested_end": meta["requested_end"]})
            else:
                video = Path(src.proxy_path) if src.proxy_path and Path(src.proxy_path).exists() else Path(src.media_path or "")
                current = (_load(clip_id).edl or {}).get("crop") or {}
                mode = ctx.params.get("mode") or (current.get("mode") if current.get("mode") != "manual" else None)
                out = _load(clip_id).edl.get("output") or {"w": 1080, "h": 1920}
                import json as _json

                result = tasks.run_vision(
                    ctx, "reframe", video, on_progress=lambda f, b=base, w=w: ctx.progress(b + w * f, step=step),
                    segments=_json.dumps(segments), mode=mode, content_type=tasks.effective_content_type(src),
                    src_w=src.width or 1920, src_h=src.height or 1080, out_w=out["w"], out_h=out["h"],
                    models_dir=settings.models_path)
                _apply_reframe(clip_id, result, start_edits)
        except jobs.JobCancelled:
            _set_work(clip_id, step, state="pending")
            for later in steps[steps.index(step) + 1:]:
                _set_work(clip_id, later, state="pending")
            raise
        except errors.AppError as exc:
            _set_work(clip_id, step, state="error", error=exc.to_dict())
            raise
        except Exception as exc:
            code = errors.PREPARE_FAILED if step == "prepare" else errors.REFRAME_FAILED
            issue = errors.issue(code, detail=tasks._short_error(exc))
            _set_work(clip_id, step, state="error", error=issue)
            for later in steps[steps.index(step) + 1:]:
                _set_work(clip_id, later, state="pending")
            raise errors.AppError(code, detail=issue["params"]["detail"]) from exc
        base += w
        ctx.progress(base, step=step, force=True)


def _apply_reframe(clip_id: str, result: dict[str, Any], start_edits: int) -> None:
    clip = _load(clip_id)
    work = {**_empty_work(), **(clip.work or {})}
    info = dict(result.get("info") or {})
    if int(work.get("crop_edits", 0)) != start_edits:
        # the user edited the framing while this ran: keep theirs
        info["applied"] = False
        work["reframe"] = {"state": "done", "error": None, "info": info}
        clip.work = work
        _save(clip)
        return
    crop = dict((clip.edl or {}).get("crop") or {})
    crop.update(mode=result.get("mode", crop.get("mode", "auto")), keyframes=result.get("keyframes") or crop.get("keyframes"),
                fit=result.get("fit", "fill"))
    if result.get("split"):
        crop["split"] = result["split"]
    src = _source(clip.source_id)
    clip.edl = edl_mod.validate({**(clip.edl or {}), "crop": crop}, source_duration=src.duration)
    info["applied"] = True
    work["reframe"] = {"state": "done", "error": None, "info": info}
    work["crop_user_edited"] = False
    clip.work = work
    _save(clip)


def reconcile() -> int:
    """At startup: clip work left queued/running without an active job goes back to pending."""
    from app.models import JOB_ACTIVE, Job

    fixed = 0
    with db.session() as s:
        active = {(j.params or {}).get("clip_id") for j in
                  s.exec(select(Job).where(Job.kind == CLIP_JOB, col(Job.status).in_(JOB_ACTIVE))).all()}
        for clip in s.exec(select(Clip)).all():
            if clip.id in active:
                continue
            work = {**_empty_work(), **(clip.work or {})}
            stuck = [st for st in WORK_STEPS if work[st]["state"] in ("queued", "running")]
            if not stuck:
                continue
            for st in stuck:
                work[st] = {**work[st], "state": "pending"}
            clip.work = work
            s.add(clip)
            fixed += 1
        s.commit()
    return fixed
