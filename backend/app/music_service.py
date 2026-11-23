"""Background music library shared by all clips: data/music/<id><ext> plus index.json.

A track's id is a hash of its content, so uploading the same file twice keeps one copy, and the
render cache (keyed by the EDL) changes when a clip switches tracks. Clips reference a track by
id (EDL `music.track_id`); deleting a track only makes clips that still use it render without
music (logged).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, AsyncIterator

from app import errors
from app.config import get_settings
from app.edl import MUSIC_ID

AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac")
MAX_BYTES = 200 * 1024 * 1024
_lock = threading.Lock()


def library_dir() -> Path:
    return get_settings().data_path / "music"


def _index_path() -> Path:
    return library_dir() / "index.json"


def _read_index() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_index_path().read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_index(index: dict[str, dict[str, Any]]) -> None:
    library_dir().mkdir(parents=True, exist_ok=True)
    tmp = _index_path().with_suffix(".json.part")
    tmp.write_text(json.dumps(index, indent=2, ensure_ascii=False), "utf-8")
    os.replace(tmp, _index_path())


def is_audio_name(name: str) -> bool:
    return Path(name).suffix.lower() in AUDIO_EXTS


def _out(track_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    return {"id": track_id, "name": meta.get("name", ""), "duration": meta.get("duration"),
            "size_bytes": meta.get("size_bytes"), "url": f"/music/{track_id}/file"}


def list_tracks() -> list[dict[str, Any]]:
    index = _read_index()
    rows = [_out(k, v) for k, v in index.items() if track_path(k, index) is not None]
    return sorted(rows, key=lambda r: r["name"].lower())


def track_path(track_id: str, index: dict[str, dict[str, Any]] | None = None) -> Path | None:
    if not MUSIC_ID.match(track_id or ""):
        return None
    meta = (index if index is not None else _read_index()).get(track_id)
    if not meta:
        return None
    path = library_dir() / f"{track_id}{meta.get('ext', '')}"
    return path if path.exists() else None


def file_for(track_id: str) -> Path:
    path = track_path(track_id)
    if path is None:
        raise errors.AppError(errors.NOT_FOUND)
    return path


def resolve(music: dict[str, Any]) -> tuple[Path, float] | None:
    """(file, duration) of the EDL's music track when it is enabled and still in the library."""
    if not music.get("enabled") or not music.get("track_id"):
        return None
    index = _read_index()
    path = track_path(music["track_id"], index)
    duration = (index.get(music["track_id"]) or {}).get("duration")
    if path is None or not duration:
        return None
    return path, float(duration)


def probe_duration(path: Path) -> float | None:
    """Audio duration in seconds (None when ffprobe finds no audio stream)."""
    try:
        r = subprocess.run([get_settings().ffprobe_bin, "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(r.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if not data.get("streams"):
        return None
    try:
        duration = float((data.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        return None
    return duration if duration > 0.1 else None


async def add_track(name: str, chunks: AsyncIterator[bytes]) -> dict[str, Any]:
    """Store an uploaded audio file in the library and return the track."""
    name = Path(name.replace("\\", "/")).name.strip() or "music.mp3"
    ext = Path(name).suffix.lower()
    if ext not in AUDIO_EXTS:
        raise errors.AppError(errors.UNSUPPORTED_FILE_TYPE, name=name)
    folder = library_dir()
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / f"upload_{os.getpid()}_{threading.get_ident()}.part"
    digest = hashlib.sha256()
    written = 0
    try:
        with part.open("wb") as f:
            async for chunk in chunks:
                written += len(chunk)
                if written > MAX_BYTES:
                    raise errors.AppError(errors.UPLOAD_FAILED)
                digest.update(chunk)
                f.write(chunk)
        if written == 0:
            raise errors.AppError(errors.UPLOAD_FAILED)
        duration = probe_duration(part)
        if duration is None:
            raise errors.AppError(errors.UNSUPPORTED_FILE_TYPE, name=name)
        track_id = digest.hexdigest()[:32]
        dest = folder / f"{track_id}{ext}"
        with _lock:
            os.replace(part, dest)
            index = _read_index()
            meta = {"name": Path(name).stem, "ext": ext, "duration": round(duration, 3), "size_bytes": written}
            index[track_id] = {**meta, **{k: v for k, v in (index.get(track_id) or {}).items() if k == "name"}}
            _write_index(index)
        return _out(track_id, index[track_id])
    except OSError as exc:
        if exc.errno == 28:
            raise errors.AppError(errors.DISK_FULL) from exc
        raise errors.AppError(errors.UPLOAD_FAILED) from exc
    finally:
        part.unlink(missing_ok=True)


def delete_track(track_id: str) -> None:
    with _lock:
        index = _read_index()
        meta = index.pop(track_id, None)
        if meta is None:
            raise errors.AppError(errors.NOT_FOUND)
        (library_dir() / f"{track_id}{meta.get('ext', '')}").unlink(missing_ok=True)
        _write_index(index)
