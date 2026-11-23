"""Step 8a - Prepare: rough-cut extraction for a clip.

Stream copy (no re-encode, seconds even for long sources) from 2 s before the clip's first segment
to 2 s after its last one. Stream copy can only start on a keyframe, so the real start of the
rough cut is the last keyframe at or before the requested start; it is measured with ffprobe and
stored in meta.json (`source_start`) so later steps can map rough-cut time back to source time.
Frame-accurate cuts happen at render time.

Cached: the rough cut is reused while the source file and the requested range are unchanged.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pipeline.ingest import run_ffmpeg, safe_unlink

PAD = 2.0
META_VERSION = 1


def rough_range(start: float, end: float, duration: float | None, pad: float = PAD) -> tuple[float, float]:
    lo = max(0.0, start - pad)
    hi = end + pad
    if duration:
        hi = min(duration, hi)
    return round(lo, 3), round(max(lo, hi), 3)


def keyframe_before(media: Path, t: float, ffprobe: str = "ffprobe") -> float:
    """Timestamp of the last video keyframe at or before t (0.0 if none found)."""
    if t <= 0:
        return 0.0
    lo = max(0.0, t - 12.0)
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
           "-show_entries", "frame=pts_time,best_effort_timestamp_time", "-of", "json",
           "-read_intervals", f"{lo:.3f}%{t + 0.05:.3f}", str(media)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        frames = json.loads(out or "{}").get("frames") or []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return lo
    times = []
    for f in frames:
        value = f.get("pts_time") or f.get("best_effort_timestamp_time")
        try:
            times.append(float(value))
        except (TypeError, ValueError):
            continue
    before = [x for x in times if x <= t + 1e-3]
    return round(max(before), 3) if before else lo


def rough_cut(media: Path, out_dir: Path, *, start: float, end: float, duration: float | None,
              ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe", pad: float = PAD,
              log: Callable[[str], None] | None = None, on_process: Any = None) -> dict[str, Any]:
    """Write out_dir/rough.mp4 + meta.json; return the meta. Reuses a cached cut for the same input."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "rough.mp4"
    meta_path = out_dir / "meta.json"
    lo, hi = rough_range(start, end, duration, pad)
    stat = media.stat()
    key = {"v": META_VERSION, "media": str(media), "size": stat.st_size, "mtime": int(stat.st_mtime),
           "requested_start": lo, "requested_end": hi}
    if out.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
            if all(meta.get(k) == v for k, v in key.items()):
                return meta
        except ValueError:
            pass
    tmp = out_dir / "rough.mp4.part"  # *.part is cleaned up at startup if a job dies mid-way
    cmd = [ffmpeg, "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", "-ss", f"{lo:.3f}", "-i", str(media),
           "-t", f"{hi - lo:.3f}", "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
           "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    try:
        run_ffmpeg(cmd, duration=hi - lo, log=log, on_process=on_process)
        tmp.replace(out)
    finally:
        safe_unlink(tmp)
    meta = {**key, "path": str(out), "source_start": keyframe_before(media, lo, ffprobe)}
    meta_path.write_text(json.dumps(meta, indent=1), "utf-8")
    return meta
