"""Moments: import proposals into the DB, manual moments, edits, and the moments.json watcher.

The watcher (a daemon thread) polls every 2 s for sources waiting on `/find-moments`. A file
counts only if it was modified after the request was written and differs from the last file that
was checked, so a stale moments.json from an earlier run is never imported by accident.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from sqlmodel import col, select

from app import db, errors, settings_store
from app.config import get_settings
from app.models import (
    CAPTION_STYLES,
    MOMENT_STATUSES,
    PLATFORMS,
    SOURCE_READY,
    SOURCE_WAITING,
    Moment,
    Source,
    utcnow,
)
from pipeline import moments as mp
from pipeline import snap

log = logging.getLogger("shortforge.moments")

POLL_SECONDS = 2.0
SETTLE_SECONDS = 0.5  # wait until the writer has finished the file


def _folder(source_id: str) -> Path:
    return get_settings().data_path / source_id


def load_words(source_id: str) -> list[dict[str, Any]]:
    path = _folder(source_id) / "transcript.json"
    try:
        return json.loads(path.read_text("utf-8")).get("words") or []
    except (OSError, ValueError):
        return []


def load_analysis(source_id: str) -> dict[str, Any]:
    path = _folder(source_id) / "analysis.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def moment_out(m: Moment) -> dict[str, Any]:
    return {
        "id": m.id, "source_id": m.source_id, "start": m.start, "end": m.end,
        "duration": round(m.end - m.start, 3), "scores": m.scores or {}, "score": m.score,
        "title": m.title, "hook": m.hook, "reason": m.reason, "category": m.category,
        "key_line": m.key_line, "status": m.status, "origin": m.origin, "provider": m.provider,
        "caption_style": m.caption_style, "platform": m.platform,
        "raw_start": m.raw_start, "raw_end": m.raw_end,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


def counts(source_id: str | None = None) -> dict[str, dict[str, int]]:
    """{source_id: {"approved": n, "total": n}} (rejected moments are not counted in total)."""
    out: dict[str, dict[str, int]] = {}
    with db.session() as s:
        q = select(Moment.source_id, Moment.status)
        if source_id:
            q = q.where(Moment.source_id == source_id)
        for sid, status in s.exec(q).all():
            c = out.setdefault(sid, {"approved": 0, "total": 0})
            if status != "rejected":
                c["total"] += 1
            if status == "approved":
                c["approved"] += 1
    return out


def list_moments(source_id: str) -> list[dict[str, Any]]:
    with db.session() as s:
        rows = s.exec(select(Moment).where(Moment.source_id == source_id).order_by(col(Moment.start))).all()
    return [moment_out(m) for m in rows]


def import_moments(source_id: str, parsed: mp.MomentsFile, provider: str) -> list[dict[str, Any]]:
    """Replace the AI proposals of a source (approved and manual moments are kept)."""
    with db.session() as s:
        src = s.get(Source, source_id)
        if src is None:
            raise errors.AppError(errors.SOURCE_NOT_FOUND)
        duration = src.duration or 0.0
        platform = src.target_platform or "youtube_shorts"
    words = load_words(source_id)
    analysis = load_analysis(source_id)
    weights = settings_store.rubric_weights()
    final = mp.finalize(parsed, words=words, silences=analysis.get("silences") or [],
                        duration=duration or float("inf"), weights=weights)
    style = settings_store.get("default_caption_style")
    with db.session() as s:
        existing = s.exec(select(Moment).where(Moment.source_id == source_id)).all()
        kept = []
        for m in existing:
            if m.status == "approved" or m.origin == "manual":
                kept.append((m.start, m.end))
            else:
                s.delete(m)
        added = 0
        for item in final:
            span = (item["start"], item["end"])
            if any(snap.overlap(span, k) > 0.6 * min(span[1] - span[0], k[1] - k[0]) for k in kept):
                continue  # already covered by a moment the user approved / made
            s.add(Moment(source_id=source_id, status="proposed", origin="ai", provider=provider,
                         caption_style=style, platform=platform, **item))
            added += 1
        src = s.get(Source, source_id)
        assert src is not None
        src.moments_summary = parsed.summary
        src.moments_notes = parsed.notes
        src.moments_provider = provider
        src.updated_at = utcnow()
        s.add(src)
        s.commit()
    log.info("imported %d moments for %s from %s", added, source_id, provider)
    return list_moments(source_id)


# --------------------------------------------------------------------------
# moments.json (claude_code provider)
# --------------------------------------------------------------------------
def result_path(source_id: str) -> Path:
    return _folder(source_id) / mp.RESULT_FILE


def check_result_file(source_id: str, *, force: bool = False) -> str:
    """Import moments.json if it is new. Returns 'imported' | 'invalid' | 'unchanged' | 'missing'."""
    from app import tasks  # local import: tasks imports this module

    src = tasks.load_source(source_id)
    step = (src.steps or {}).get("moments", {})
    path = result_path(source_id)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return "missing"
    requested_at = float(step.get("requested_at") or 0)
    if not force:
        if mtime <= requested_at or mtime == step.get("checked_mtime"):
            return "unchanged"
        if time.time() - mtime < SETTLE_SECONDS:
            return "unchanged"
    try:
        text = path.read_text("utf-8")
        parsed = mp.parse_moments(text, source_id=source_id, duration=src.duration)
    except (OSError, mp.MomentsInvalid) as exc:
        detail = str(exc)[:300] or type(exc).__name__
        tasks.set_step(source_id, "moments", checked_mtime=mtime, error=errors.issue(errors.MOMENTS_INVALID, detail=detail))
        log.warning("invalid moments.json for %s: %s", source_id, detail)
        return "invalid"
    import_moments(source_id, parsed, "claude_code")
    notices = []
    count = len(parsed.moments)
    if count < settings_store.get("moments_wanted")[0]:
        notices.append(errors.issue(errors.MOMENTS_FEWER_THAN_WANTED, count=count))
    tasks.set_step(source_id, "moments", state="done", progress=1.0, error=None, stage=None,
                   checked_mtime=mtime, notices=notices)
    tasks.update_source(source_id, status=SOURCE_READY, error=None)
    return "imported"


class ResultWatcher:
    def __init__(self, interval: float = POLL_SECONDS):
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="moments-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def tick(self) -> None:
        with db.session() as s:
            waiting = [src.id for src in s.exec(select(Source).where(Source.status == SOURCE_WAITING)).all()]
        for source_id in waiting:
            try:
                check_result_file(source_id)
            except Exception:  # noqa: BLE001 - never kill the watcher thread
                log.exception("moments watcher failed for %s", source_id)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.tick()


# --------------------------------------------------------------------------
# Manual moments and edits
# --------------------------------------------------------------------------
def _snap(source_id: str, start: float, end: float, duration: float | None) -> tuple[float, float]:
    words = load_words(source_id)
    silences = load_analysis(source_id).get("silences") or []
    return snap.snap_moment(start, end, words, silences, duration=duration)


def _clean_range(start: float, end: float, duration: float | None) -> tuple[float, float]:
    limit = duration if duration else float("inf")
    start = max(0.0, min(float(start), limit))
    end = max(0.0, min(float(end), limit))
    if end - start < 0.5:
        raise errors.AppError(errors.VALIDATION_ERROR, fields="start,end")
    return round(start, 3), round(end, 3)


def create_manual(source_id: str, *, start: float, end: float, title: str = "", hook: str = "",
                  snap_bounds: bool = True, status: str = "approved") -> dict[str, Any]:
    from app import tasks

    src = tasks.load_source(source_id)
    raw = _clean_range(start, end, src.duration)
    bounds = _snap(source_id, *raw, src.duration) if snap_bounds else raw
    if status not in MOMENT_STATUSES:
        raise errors.AppError(errors.VALIDATION_ERROR, fields="status")
    m = Moment(source_id=source_id, start=bounds[0], end=bounds[1], raw_start=raw[0], raw_end=raw[1],
               title=title.strip()[:120], hook=hook.strip()[:160], category="manual", origin="manual",
               status=status, scores={}, score=0, provider=None,
               caption_style=settings_store.get("default_caption_style"),
               platform=src.target_platform or "youtube_shorts")
    with db.session() as s:
        s.add(m)
        s.commit()
        s.refresh(m)
    return moment_out(m)


EDITABLE_TEXT = {"title": 120, "hook": 160, "reason": 600}


def update_moment(moment_id: int, changes: dict[str, Any], *, snap_bounds: bool = True) -> dict[str, Any]:
    from app import tasks

    with db.session() as s:
        m = s.get(Moment, moment_id)
        if m is None:
            raise errors.AppError(errors.MOMENT_NOT_FOUND)
        if "start" in changes or "end" in changes:
            src = tasks.load_source(m.source_id)
            raw = _clean_range(changes.get("start", m.start), changes.get("end", m.end), src.duration)
            bounds = _snap(m.source_id, *raw, src.duration) if snap_bounds else raw
            m.start, m.end = bounds
            m.raw_start, m.raw_end = raw
        for key, limit in EDITABLE_TEXT.items():
            if key in changes and changes[key] is not None:
                setattr(m, key, str(changes[key]).strip()[:limit])
        if "status" in changes:
            if changes["status"] not in MOMENT_STATUSES:
                raise errors.AppError(errors.VALIDATION_ERROR, fields="status")
            m.status = changes["status"]
        if "caption_style" in changes:
            if changes["caption_style"] not in CAPTION_STYLES:
                raise errors.AppError(errors.VALIDATION_ERROR, fields="caption_style")
            m.caption_style = changes["caption_style"]
        if "platform" in changes:
            if changes["platform"] not in PLATFORMS:
                raise errors.AppError(errors.VALIDATION_ERROR, fields="platform")
            m.platform = changes["platform"]
        m.updated_at = utcnow()
        s.add(m)
        s.commit()
        s.refresh(m)
        return moment_out(m)


def delete_moment(moment_id: int) -> None:
    with db.session() as s:
        m = s.get(Moment, moment_id)
        if m is None:
            raise errors.AppError(errors.MOMENT_NOT_FOUND)
        s.delete(m)
        s.commit()


def delete_for_source(source_id: str) -> None:
    with db.session() as s:
        for m in s.exec(select(Moment).where(Moment.source_id == source_id)).all():
            s.delete(m)
        s.commit()


def rescore_all() -> None:
    """Recompute every AI moment's total after the rubric weights changed in Settings."""
    weights = settings_store.rubric_weights()
    with db.session() as s:
        for m in s.exec(select(Moment).where(Moment.origin == "ai")).all():
            if m.scores:
                m.score = mp.compute_score(m.scores, weights)
                s.add(m)
        s.commit()
