"""Cache size + cleanup for the Settings page.

Only files the app can rebuild for free are clearable:
- transcripts:     data/cache/transcripts (faster-whisper results; re-transcribing takes time)
- remotion_bundle: data/cache/remotion_bundle (rebuilt on the next Remotion render)
- logs:            data/logs (job logs of finished jobs)
- voice_previews:  data/voice_cache/preview_*.mp3 (voice samples, free to fetch again)

Shown but never cleared here: generated voiceovers / isolated voices (they cost ElevenLabs credits
and clips point at them), whisper models (large downloads), rendered outputs.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from sqlmodel import col, select

from app import db, errors
from app.config import get_settings
from app.models import Job

JOB_ACTIVE = ("queued", "running")


def _size(paths: list[Path]) -> tuple[int, int]:
    total = count = 0
    for p in paths:
        if p.is_file():
            total += p.stat().st_size
            count += 1
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
                    count += 1
    return total, count


def _active_log_names() -> set[str]:
    with db.session() as s:
        ids = s.exec(select(Job.id).where(col(Job.status).in_(JOB_ACTIVE))).all()
    return {f"job_{i}.log" for i in ids}


def _entries() -> dict[str, tuple[Callable[[], list[Path]], bool]]:
    data = get_settings().data_path
    vc = data / "voice_cache"

    def logs() -> list[Path]:
        folder = data / "logs"
        active = _active_log_names()
        return [p for p in folder.glob("*") if p.is_file() and p.name not in active] if folder.exists() else []

    def paid() -> list[Path]:
        return [p for p in vc.glob("*") if p.is_file() and not p.name.startswith("preview_")] if vc.exists() else []

    return {
        "transcripts": (lambda: [data / "cache" / "transcripts"], True),
        "remotion_bundle": (lambda: [data / "cache" / "remotion_bundle"], True),
        "logs": (logs, True),
        "voice_previews": (lambda: list(vc.glob("preview_*.mp3")) if vc.exists() else [], True),
        "voice_generated": (paid, False),
        "whisper_models": (lambda: [get_settings().models_path], False),
        "outputs": (lambda: [data / "output"], False),
    }


def summary() -> dict[str, Any]:
    items = []
    for key, (paths, clearable) in _entries().items():
        size, files = _size(paths())
        items.append({"key": key, "bytes": size, "files": files, "clearable": clearable})
    return {"items": items, "clearable_bytes": sum(i["bytes"] for i in items if i["clearable"])}


def clear(keys: list[str] | None = None) -> dict[str, Any]:
    entries = _entries()
    wanted = [k for k in (keys or [k for k, (_, c) in entries.items() if c]) if k in entries and entries[k][1]]
    freed = 0
    failed: list[str] = []
    for key in wanted:
        for p in entries[key][0]():
            size, _ = _size([p])
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                elif p.exists():
                    p.unlink()
                freed += size
            except OSError:
                failed.append(p.name)  # in use (e.g. Windows file lock); try again later
    out = {**summary(), "cleared": wanted, "freed_bytes": freed}
    if failed:
        out["warning"] = errors.AppError(errors.CACHE_CLEAR_FAILED, detail=", ".join(failed[:5])).to_dict()
    return out
